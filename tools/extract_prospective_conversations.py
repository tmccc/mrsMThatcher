#!/usr/bin/env python3
"""Build a private, deterministic corpus from retained production logs only.

The extractor is deliberately separate from the bot.  It has no network,
provider, configuration, or production-state dependency.  Its only production
inputs are the exact ``mrsMThatcher.log`` rotation family selected below.
"""

from __future__ import annotations

import argparse
import ast
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 4
EXTRACTOR_VERSION = "prospective-conversation-extractor-v4"
PARSER_VERSION = "prospective-conversation-log-parser-v4"
REGISTERED_REBUILD_SOURCE = (
    3,
    "prospective-conversation-extractor-v3",
    "prospective-conversation-log-parser-v3",
)
HASH_BLOCK_SIZE = 1024 * 1024
X_SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
X_SNOWFLAKE_MIN_DIGITS = 15
X_SNOWFLAKE_MAX_DIGITS = 20
X_SNOWFLAKE_FUTURE_SKEW = timedelta(minutes=5)
MAX_SEND_ATTEMPTS_PER_TARGET = 5
MAX_IGNORED_STRUCTURED_EVENT_KINDS = 64
MAX_SIBLING_CONTEXT_REFS = 32
MAX_SIBLING_CONTEXT_TEXT_CHARS = 500
MAX_HANDOFF_CONTEXT_REFS = 8
MAX_ACCOUNT_EVIDENCE_CONFLICTS = 32
MAX_ACCOUNT_CONFLICT_TEXT_CHARS = 500
MAX_REPLY_MEDIA_CONTEXT_OBSERVATIONS = 16
MAX_REPLY_VISUAL_DESCRIPTION_ATTEMPTS = 16
MAX_REPLY_VISUAL_REPORTED_IMAGES = 2_147_483_647
MAX_REPLY_VISUAL_SUPPORTED_IMAGES = 2
MAX_REPLY_VISUAL_SCHEMA_VERSION = 2_147_483_647
REPLY_VISUAL_DESCRIPTION_EVENT_FIELDS = frozenset(
    {
        "analysis",
        "analysis_schema_version",
        "description_sha256",
        "event",
        "lane",
        "status",
        "supplied_image_count",
        "target_id",
        "visual_analysis_call_count",
    }
)
REPLY_VISUAL_DESCRIPTION_STATUSES = frozenset(
    {
        "analysed",
        "provider_error",
        "invalid_response",
        "invalid_supplied_media",
        "paused",
    }
)
REPLY_VISUAL_LANES = frozenset(
    {"mention", "hot-post reply", "quote-tweet reply"}
)
REPLY_MEDIA_CONTEXT_OBSERVATION_FIELDS = frozenset(
    {
        "lane",
        "mode",
        "observed_at",
        "photo_count",
        "record_fingerprint",
        "status",
    }
)
REPLY_VISUAL_DESCRIPTION_OBSERVATION_FIELDS = frozenset(
    {
        "analysis_schema_version",
        "description_sha256",
        "lane",
        "observed_at",
        "record_fingerprint",
        "status",
        "supplied_image_count",
        "visual_analysis_call_count",
    }
)
REPLY_VISUAL_CONTEXT_SUMMARY_FIELDS = frozenset(
    {
        "analysis_attempt_count",
        "analysis_history_complete",
        "analysis_observation_status",
        "analysis_schema_versions",
        "collection_history_complete",
        "distinct_successful_description_count",
        "latest_analysis_status",
        "latest_collection_status",
        "native_photo_count_max",
        "omitted_media_observation_count",
        "omitted_visual_event_count",
        "successful_analysis_count",
        "successful_description_sha256s",
        "visual_event_count",
    }
)
REPLY_VISUAL_METADATA_FIELDS = frozenset(
    {
        "reply_media_context_observations",
        "reply_media_context_other_count",
        "reply_visual_context_summary",
        "reply_visual_description_attempts",
        "reply_visual_description_other_count",
    }
)
REPLY_VISUAL_ANALYSIS_OBSERVATION_STATUSES = frozenset(
    {
        "analysed",
        "attempted_not_analysed",
        "history_incomplete",
        "not_applicable",
        "not_attempted",
        "not_observed",
    }
)
SOURCE_STALE_WARNING_SECONDS = 6 * 60 * 60
RECENT_BATCH_RETENTION = timedelta(hours=72)
DAILY_BATCH_RETENTION = timedelta(days=90)
MAX_AUTOMATIC_BATCH_BYTES = 10 * 1024 * 1024 * 1024
MIN_FILESYSTEM_FREE_BYTES = 10 * 1024 * 1024 * 1024
LOG_BASENAME_RE = re.compile(r"mrsMThatcher\.log(?:\.([0-9]+))?\Z")
BATCH_ID_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}\Z")
LOG_HEADER_RE = re.compile(
    rb"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    rb"(?P<level>[A-Z]+)\s+(?P<src>.+?)(?::(?P<line>\d+))? - "
    rb"(?P<msg>.*?)(?:\r?\n)?\Z"
)
TIMESTAMP_PREFIX_RE = re.compile(rb"^\d{4}-\d{2}-\d{2}[ T]")
LONDON = ZoneInfo("Europe/London")

BATCH_FILES = (
    "manifest.json",
    "source-manifest.json",
    "canonical-posts.jsonl",
    "conversations.jsonl",
    "review-candidates.jsonl",
    "status.json",
    "extraction-report.md",
)
BATCH_HASHED_FILES = tuple(name for name in BATCH_FILES if name != "manifest.json")
PACK_FILES = (
    "manifest.json",
    "conversations.jsonl",
    "review-candidates.jsonl",
    "review-pack.md",
)
PACK_HASHED_FILES = tuple(name for name in PACK_FILES if name != "manifest.json")

FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "author_id",
        "raw_author_id",
        "raw_user_id",
        "user_id",
        "contributor_id",
        "pseudonym_key",
        "api_key",
        "access_token",
    }
)

CONSIDER_RE = re.compile(
    r"Considering (mention|hot_post_reply) id=(\d+) author_id=([^\s]+) text=(.*)$",
    re.S,
)
QUOTE_RE = re.compile(
    r"Considering quote tweet id=(\d+) author_id=([^\s]+) "
    r"original_post_id=(\d+) text=(.*)$",
    re.S,
)
REPLY_MEDIA_CONTEXT_RE = re.compile(
    r"Reply media context(?P<unavailable> unavailable)? "
    r"lane=(?P<lane>[^\s]+) target_id=(?P<target_id>[^\s]+) "
    r"(?:(?P<photos>photos)|(?P<photos_expected>photos_expected))="
    r"(?P<photo_count>\d+) mode=(?P<mode>[^\s]+) "
    r"status=(?P<status>supplied|unavailable)\Z"
)
CONTEXT_RE = re.compile(
    r"Built AI reply context for (?:mention|hot.post.reply) (\d+)\. "
    r"chain_items=(\d+) immediate_parent=([^\s]+) quoted=(True|False)",
)
SINGLE_CALL_CONTEXT_RE = re.compile(
    r"Built single-call reply context target_id=(\d+) turns=(\d+) "
    r"root_id=([^\s]+) parent_id=([^\s]+)",
)
GENERATED_RE = re.compile(
    r"Generated reply to (mention|hot_post_reply) (\d+): (.*)$", re.S
)
GENERATED_QUOTE_RE = re.compile(r"Generated reply to quote tweet (\d+): (.*)$", re.S)
PROMOTED_RE = re.compile(
    r"Promoted conversational reply receipt to confirmed source=([^\s]+) "
    r"target_id=([^\s]+) reply_post_id=([^\s]+)"
)
CREATE_ATTEMPT_RE = re.compile(
    r"Creating X post with durable transport journal\..*?"
    r"\blane=([^\s]+)\s+transaction_id=([0-9a-f]{64})\s+"
    r"reply_to_id=([^\s]+).*?\btext=(.*)$",
    re.S,
)
CREATED_ID_RE = re.compile(
    r"Created X post successfully\. response=.*?(?:'id'|\"id\")\s*:\s*"
    r"(?:'|\")([^'\"\s,}]+)(?:'|\")",
    re.S,
)
FAILED_GENERATED_REPLY_RE = re.compile(
    r"(?:Unexpected failure posting generated reply|Failed to post generated reply)"
)
REMOVED_SENDING_RECEIPT_RE = re.compile(
    r"Removed conversational reply sending receipt disposition=([^\s]+) "
    r"source=([^\s]+) target_id=([^\s]+)"
)
MAIN_ATTEMPT_WRITTEN_RE = re.compile(
    r"Wrote main-post sending receipt lane=(quote_image|daily_meme) "
    r"attempt_id=([0-9a-f]{64})\b"
)
MAIN_ATTEMPT_ATTEMPTING_RE = re.compile(
    r"Promoted main-post receipt to attempting lane=(quote_image|daily_meme) "
    r"attempt_id=([0-9a-f]{64})\b"
)
MAIN_ATTEMPT_CONFIRMED_RE = re.compile(
    r"Promoted main-post attempt to confirmed pending-schedule receipt "
    r"lane=(quote_image|daily_meme) attempt_id=([0-9a-f]{64}) "
    r"post_id=(\d+)\b"
)
MAIN_SCHEDULE_FINALISED_RE = re.compile(
    r"Finalised confirmed pending-schedule receipt lane=(quote_image|daily_meme) "
    r"post_id=(\d+)\b"
)

CORRECTION_CUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("not what i said", re.compile(r"\bnot what i said\b", re.I)),
    ("that is not what i said", re.compile(r"\bthat is not what i said\b", re.I)),
    ("that isn't what i said", re.compile(r"\bthat isn['’]t what i said\b", re.I)),
    ("i said", re.compile(r"\bi said\b", re.I)),
    ("i did not say", re.compile(r"\bi did not say\b", re.I)),
    ("i didn't say", re.compile(r"\bi didn['’]t say\b", re.I)),
    ("my point is", re.compile(r"\bmy point is\b", re.I)),
    ("my point was", re.compile(r"\bmy point was\b", re.I)),
    ("the point is", re.compile(r"\bthe point is\b", re.I)),
    ("you are answering", re.compile(r"\byou are answering\b", re.I)),
    ("you're answering", re.compile(r"\byou['’]re answering\b", re.I)),
    ("you have changed", re.compile(r"\byou have changed\b", re.I)),
    ("you've changed", re.compile(r"\byou['’]ve changed\b", re.I)),
    ("that does not answer", re.compile(r"\bthat does not answer\b", re.I)),
    ("that doesn't answer", re.compile(r"\bthat doesn['’]t answer\b", re.I)),
    ("rather than", re.compile(r"\brather than\b", re.I)),
    ("the distinction", re.compile(r"\bthe distinction\b", re.I)),
    ("i mean", re.compile(r"\bi mean\b", re.I)),
    ("i'm not talking about", re.compile(r"\bi['’]m not talking about\b", re.I)),
    ("i am not talking about", re.compile(r"\bi am not talking about\b", re.I)),
    ("not ... but", re.compile(r"\bnot\b.{0,120}\bbut\b", re.I | re.S)),
)

REVIEW_REASON_ORDER = (
    "same_author_path_continuation",
    "multiple_account_replies_on_path",
    "third_or_later_substantive_path_turn",
    "explicit_correction_cue",
    "post_clarification_continuation",
    "external_author_handoff_context",
    "sibling_branch_context",
    "partial_path_reconstruction",
    "ambiguous_parentage",
)

ACCOUNT_EVIDENCE_AUTHORITY_RANK = {
    "mention_observation": 0,
    "legacy_confirmed_sequence": 1,
    "structured_confirmation": 2,
}
ACCOUNT_GRAPH_FIELDS = (
    "lane",
    "parent_post_id",
    "parent_observation_status",
    "root_post_id",
    "conversation_id",
    "quote_id",
)
ACCOUNT_CONTENT_FIELDS = (
    "text",
    "text_source",
    "public_text",
    "visible_media_text",
)
ACCOUNT_UNAVAILABLE_TEXT_WARNINGS = frozenset(
    {
        "confirmed_account_root_text_unavailable",
        "legacy_account_root_text_unavailable",
        "confirmed_historical_context_text_unavailable",
        "legacy_historical_context_text_unavailable",
    }
)


class ExtractorError(RuntimeError):
    """A safe, user-facing extraction refusal."""


class SourceChanged(ExtractorError):
    """A source was truncated or replaced during its bounded read."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(value: str | bytes) -> Any:
    """Load strict JSON, rejecting duplicate keys and non-finite numbers."""
    return json.loads(
        value,
        object_pairs_hook=_unique_json_object,
        parse_constant=_reject_json_constant,
    )


def canonical_json_bytes(value: Any, *, newline: bool = True) -> bytes:
    """Serialize a value as canonical UTF-8 JSON with an optional newline."""
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + ("\n" if newline else "")).encode("utf-8")


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Serialize mapping rows as canonical newline-terminated JSON Lines."""
    return b"".join(canonical_json_bytes(dict(row)) for row in rows)


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 hexadecimal digest of bytes."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_BLOCK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *values: object) -> str:
    """Derive a stable prefixed identifier from ordered values."""
    material = "\x1f".join(str(value) for value in values).encode(
        "utf-8", "surrogatepass"
    )
    return f"{prefix}-{sha256_bytes(material)}"


def parse_aware_timestamp(value: str, *, option: str) -> datetime:
    """Parse an aware ISO-8601 timestamp and normalize it to UTC."""
    candidate = value.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ExtractorError(f"{option} is not a valid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExtractorError(f"{option} must include a timezone offset or Z")
    return parsed.astimezone(timezone.utc)


def parse_optional_timestamp(value: Any, *, assume_london: bool = False) -> datetime | None:
    """Parse an optional timestamp to UTC, returning ``None`` if unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=LONDON if assume_london else timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_utc(value: datetime | None) -> str | None:
    """Format an optional aware datetime as an ISO-8601 UTC timestamp."""
    if value is None:
        return None
    normalised = value.astimezone(timezone.utc)
    if normalised.microsecond:
        text = normalised.isoformat(timespec="microseconds")
    else:
        text = normalised.isoformat(timespec="seconds")
    return text.replace("+00:00", "Z")


def compact_utc(value: datetime) -> str:
    """Format a datetime as a compact second-resolution UTC timestamp."""
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _absolute_without_following(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_real_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExtractorError(f"cannot inspect {label} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExtractorError(f"{label} must be a non-symlink directory: {path}")


def validate_root_relationship(project_dir: Path, output_root: Path) -> tuple[Path, Path]:
    """Validate that the output root lies outside the production project."""
    project = _absolute_without_following(project_dir)
    root = _absolute_without_following(output_root)
    _require_real_directory(project, label="project directory")
    if _is_within(root, project):
        raise ExtractorError("--output-root must not be inside the production project directory")
    return project, root


def _mkdir_private(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError as exc:
        raise ExtractorError(f"cannot create private directory {path}: {exc}") from exc
    _require_real_directory(path, label="private output directory")
    os.chmod(path, 0o700, follow_symlinks=False)


def ensure_private_layout(root: Path) -> None:
    """Create or secure the extractor's private output directory layout."""
    parent = root.parent
    _require_real_directory(parent, label="output parent")
    if root.exists() or root.is_symlink():
        _require_real_directory(root, label="output root")
        os.chmod(root, 0o700, follow_symlinks=False)
    else:
        _mkdir_private(root)
    for name in ("state", "batches", "review-packs"):
        child = root / name
        if child.exists() or child.is_symlink():
            _require_real_directory(child, label=name)
            os.chmod(child, 0o700, follow_symlinks=False)
        else:
            _mkdir_private(child)


def _source_number(name: str) -> int | None:
    match = LOG_BASENAME_RE.fullmatch(name)
    if not match:
        return None
    if match.group(1) is None:
        return 0
    number = int(match.group(1))
    return number if 1 <= number <= 100 else None


def source_sort_key(path: Path) -> tuple[int, str]:
    """Return deterministic precedence for active and rotated log files."""
    number = _source_number(path.name)
    if number is None:
        return (1, path.name)
    return (-number, path.name)


@dataclass(frozen=True)
class SourceWarning:
    """Describe a non-fatal source discovery or read warning."""

    basename: str
    kind: str
    detail: str

    def as_json(self) -> dict[str, str]:
        """Return the warning as a stable JSON-compatible mapping."""
        return {"basename": self.basename, "detail": self.detail, "kind": self.kind}


@dataclass(frozen=True)
class SourceRead:
    """Hold one coherently read retained-log source and its identity."""

    path: Path
    basename: str
    device: int
    inode: int
    size: int
    modification_time_ns: int
    content_sha256: str
    complete_line_count: int
    incomplete_trailing_line_ignored: bool
    complete_data: bytes
    inventory_retry_required: bool

    def manifest_row(self) -> dict[str, Any]:
        """Return source identity and completeness fields for a manifest."""
        return {
            "absolute_path": str(self.path),
            "basename": self.basename,
            "complete_line_count": self.complete_line_count,
            "content_sha256": self.content_sha256,
            "device": self.device,
            "incomplete_trailing_line_ignored": self.incomplete_trailing_line_ignored,
            "inode": self.inode,
            "modification_time_ns": self.modification_time_ns,
            "size": self.size,
        }


@dataclass(frozen=True)
class SourceInventory:
    """Hold a coherent retained-log inventory and its scan warnings."""

    files: tuple[SourceRead, ...]
    warnings: tuple[SourceWarning, ...]
    retry_count: int


def discover_log_sources(project_dir: Path) -> tuple[list[Path], list[SourceWarning]]:
    """Select only the active log and numeric rotations 1 through 100."""
    selected: list[Path] = []
    warnings: list[SourceWarning] = []
    try:
        entries = list(os.scandir(project_dir))
    except OSError as exc:
        raise ExtractorError(f"cannot enumerate project directory {project_dir}: {exc}") from exc
    for entry in entries:
        number = _source_number(entry.name)
        if number is None:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            warnings.append(SourceWarning(entry.name, "source_stat_failed", str(exc)))
            continue
        if stat.S_ISLNK(info.st_mode):
            warnings.append(
                SourceWarning(entry.name, "source_symlink_rejected", "symlinks are not followed")
            )
            continue
        if not stat.S_ISREG(info.st_mode):
            warnings.append(
                SourceWarning(
                    entry.name,
                    "source_non_regular_rejected",
                    "source is not a regular file",
                )
            )
            continue
        selected.append(project_dir / entry.name)
    selected.sort(key=source_sort_key)
    warnings.sort(key=lambda item: (item.basename, item.kind, item.detail))
    return selected, warnings


def _entry_identities(project_dir: Path) -> tuple[tuple[str, int, int], ...]:
    paths, _warnings = discover_log_sources(project_dir)
    identities: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            info = path.lstat()
        except OSError:
            continue
        identities.append((path.name, int(info.st_dev), int(info.st_ino)))
    return tuple(identities)


def read_source_prefix(
    path: Path,
    *,
    after_open: Callable[[int, os.stat_result], None] | None = None,
) -> SourceRead:
    """Read at most the initial size from one regular source without locking it."""
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceChanged(f"cannot open source read-only {path}: {exc}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise SourceChanged(f"source is not a regular file: {path}")
        initial_size = int(initial.st_size)
        if after_open is not None:
            after_open(descriptor, initial)
        chunks: list[bytes] = []
        remaining = initial_size
        while remaining:
            chunk = os.read(descriptor, min(HASH_BLOCK_SIZE, remaining))
            if not chunk:
                raise SourceChanged(f"source was truncated during bounded read: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if int(after.st_size) < initial_size:
            raise SourceChanged(f"source was truncated during bounded read: {path}")
        data = b"".join(chunks)
        retry_required = False
        try:
            current = path.lstat()
        except FileNotFoundError:
            retry_required = True
        except OSError as exc:
            raise SourceChanged(f"cannot recheck source path {path}: {exc}") from exc
        else:
            retry_required = (
                int(current.st_dev) != int(initial.st_dev)
                or int(current.st_ino) != int(initial.st_ino)
                or not stat.S_ISREG(current.st_mode)
            )
        complete_end = data.rfind(b"\n") + 1
        complete = data[:complete_end] if complete_end else b""
        return SourceRead(
            path=_absolute_without_following(path),
            basename=path.name,
            device=int(initial.st_dev),
            inode=int(initial.st_ino),
            size=initial_size,
            modification_time_ns=int(initial.st_mtime_ns),
            content_sha256=sha256_bytes(data),
            complete_line_count=complete.count(b"\n"),
            incomplete_trailing_line_ignored=complete_end < len(data),
            complete_data=complete,
            inventory_retry_required=retry_required,
        )
    finally:
        os.close(descriptor)


def collect_source_inventory(project_dir: Path) -> SourceInventory:
    """Read a coherent source inventory, retrying one rotation/replacement race."""
    last_detail = "source inventory changed"
    for attempt in range(2):
        paths, warnings = discover_log_sources(project_dir)
        before = _entry_identities(project_dir)
        reads: list[SourceRead] = []
        changed = False
        try:
            for path in paths:
                source = read_source_prefix(path)
                reads.append(source)
                changed = changed or source.inventory_retry_required
        except SourceChanged as exc:
            last_detail = str(exc)
            changed = True
        after = _entry_identities(project_dir)
        if before != after:
            changed = True
            last_detail = "source directory identities changed during scan"
        if not changed:
            return SourceInventory(tuple(reads), tuple(warnings), attempt)
        if attempt == 0:
            continue
    raise SourceChanged(f"production log inventory remained unstable after one retry: {last_detail}")


@dataclass(frozen=True)
class LogRecord:
    """Represent one canonical parsed production-log record."""

    timestamp: str
    timestamp_value: datetime
    original_timestamp_text: str
    level: str
    source: str
    line: int | None
    ordinal: int
    message: str
    record_fingerprint: str
    structured_event: dict[str, Any] | None
    warnings: tuple[str, ...]

    def provenance(self) -> dict[str, str]:
        """Return stable source provenance for derived evidence."""
        return {
            "observed_at": self.timestamp,
            "record_fingerprint": self.record_fingerprint,
            "source": self.source,
        }


def _log_timestamp(value: bytes) -> tuple[datetime, str]:
    text = value.decode("ascii")
    parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LONDON)
    utc = parsed.astimezone(timezone.utc)
    return utc, str(format_utc(utc))


def parse_log_records(data: bytes) -> tuple[list[LogRecord], list[dict[str, Any]]]:
    """Parse complete physical lines into canonical multi-line log records."""
    records: list[LogRecord] = []
    source_warnings: list[dict[str, Any]] = []
    current: list[bytes] = []
    header: re.Match[bytes] | None = None
    current_warnings: list[str] = []

    def flush() -> None:
        nonlocal current, header, current_warnings
        if header is None:
            return
        raw = b"".join(current)
        timestamp_value, timestamp = _log_timestamp(header.group("ts"))
        first = current[0]
        ending = b"\r\n" if first.endswith(b"\r\n") else b"\n"
        continuation = b"".join(current[1:])
        message_bytes = header.group("msg") + ((ending + continuation) if continuation else b"")
        message = message_bytes.decode("utf-8", "replace")
        structured: dict[str, Any] | None = None
        warnings = list(current_warnings)
        if message.startswith("EVENT "):
            try:
                candidate = strict_json_loads(message[6:])
                if not isinstance(candidate, dict):
                    raise ValueError("EVENT payload is not an object")
                structured = candidate
            except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
                warnings.append(f"malformed_structured_event:{type(exc).__name__}")
        line_value = header.group("line")
        records.append(
            LogRecord(
                timestamp=timestamp,
                timestamp_value=timestamp_value,
                original_timestamp_text=header.group("ts").decode("ascii"),
                level=header.group("level").decode("ascii"),
                source=header.group("src").decode("utf-8", "replace").strip(),
                line=int(line_value) if line_value else None,
                ordinal=len(records) + 1,
                message=message,
                record_fingerprint=sha256_bytes(raw),
                structured_event=structured,
                warnings=tuple(sorted(set(warnings))),
            )
        )
        current = []
        header = None
        current_warnings = []

    for line_number, raw_line in enumerate(data.splitlines(keepends=True), start=1):
        match = LOG_HEADER_RE.match(raw_line)
        if match:
            flush()
            current = [raw_line]
            header = match
            continue
        if header is None:
            source_warnings.append(
                {
                    "byte_sha256": sha256_bytes(raw_line),
                    "kind": (
                        "malformed_timestamp_prefix"
                        if TIMESTAMP_PREFIX_RE.match(raw_line)
                        else "leading_unparsed_material"
                    ),
                    "line": line_number,
                }
            )
        else:
            current.append(raw_line)
            if TIMESTAMP_PREFIX_RE.match(raw_line):
                current_warnings.append("malformed_timestamp_prefix_in_record")
    flush()
    source_warnings.sort(key=lambda item: canonical_json_bytes(item, newline=False))
    return records, source_warnings


def decode_literal(value: str) -> str:
    """Decode a Python string literal when possible, otherwise return input."""
    candidate = value.strip()
    try:
        decoded = ast.literal_eval(candidate)
    except (SyntaxError, ValueError):
        return candidate
    return decoded if isinstance(decoded, str) else candidate


def normalise_lane(value: Any) -> str:
    """Map observed conversational lane labels to canonical display names."""
    lane = str(value or "other").strip().casefold().replace("_", "-")
    if "hot-post" in lane:
        return "hot-post reply"
    if lane.startswith("quote"):
        return "quote-tweet reply"
    if lane in {"mention", "mention-reply", "normal", "normal-reply"} or lane.startswith("mention+"):
        return "mention"
    return lane or "other conversational lane"


def parse_reply_media_context_observation(
    record: LogRecord,
) -> tuple[str, dict[str, Any]] | None:
    """Parse one exact safe supplied/unavailable legacy media observation."""
    match = REPLY_MEDIA_CONTEXT_RE.fullmatch(record.message)
    if match is None:
        return None
    status = match.group("status")
    unavailable_form = match.group("unavailable") is not None
    supplied_count_field = match.group("photos") is not None
    expected_count_field = match.group("photos_expected") is not None
    if (
        match.group("mode") != "multimodal"
        or (status == "supplied")
        != (not unavailable_form and supplied_count_field and not expected_count_field)
        or (status == "unavailable")
        != (unavailable_form and expected_count_field and not supplied_count_field)
    ):
        return None
    photo_count = int(match.group("photo_count"))
    if not 1 <= photo_count <= MAX_REPLY_VISUAL_REPORTED_IMAGES:
        return None
    target_id = match.group("target_id").strip()
    lane = normalise_lane(match.group("lane"))
    if not target_id or lane not in REPLY_VISUAL_LANES:
        return None
    return (
        target_id,
        {
            "lane": lane,
            "mode": "multimodal",
            "observed_at": record.timestamp,
            "photo_count": photo_count,
            "record_fingerprint": record.record_fingerprint,
            "status": status,
        },
    )


def _validated_reply_visual_description_metadata(
    value: Mapping[str, Any],
    *,
    normalise_lane_value: bool,
    allow_empty_hash: bool,
) -> dict[str, Any] | None:
    """Apply the producer's status-specific visual metadata contract once."""
    raw_lane = value.get("lane")
    if not isinstance(raw_lane, str):
        return None
    lane = normalise_lane(raw_lane) if normalise_lane_value else raw_lane
    status = value.get("status")
    supplied_image_count = value.get("supplied_image_count")
    schema_version = value.get("analysis_schema_version")
    call_count = value.get("visual_analysis_call_count")
    if (
        lane not in REPLY_VISUAL_LANES
        or status not in REPLY_VISUAL_DESCRIPTION_STATUSES
        or type(supplied_image_count) is not int
        or supplied_image_count < 0
        or supplied_image_count > MAX_REPLY_VISUAL_REPORTED_IMAGES
        or type(schema_version) is not int
        or schema_version <= 0
        or schema_version > MAX_REPLY_VISUAL_SCHEMA_VERSION
        or type(call_count) is not int
        or call_count < 0
        or call_count > 1
    ):
        return None
    raw_hash = value.get("description_sha256")
    if raw_hash is None or (allow_empty_hash and raw_hash == ""):
        description_sha256: str | None = None
    elif isinstance(raw_hash, str) and re.fullmatch(r"[0-9a-f]{64}", raw_hash):
        description_sha256 = raw_hash
    else:
        return None

    if status == "analysed":
        valid_shape = (
            1 <= supplied_image_count <= MAX_REPLY_VISUAL_SUPPORTED_IMAGES
            and call_count == 1
            and description_sha256 is not None
        )
    elif description_sha256 is not None:
        valid_shape = False
    elif status in {"provider_error", "invalid_response"}:
        valid_shape = (
            1 <= supplied_image_count <= MAX_REPLY_VISUAL_SUPPORTED_IMAGES
            and call_count == 1
        )
    elif status == "paused":
        valid_shape = (
            1 <= supplied_image_count <= MAX_REPLY_VISUAL_SUPPORTED_IMAGES
            and call_count == 0
        )
    else:
        valid_shape = status == "invalid_supplied_media" and call_count == 0
    if not valid_shape:
        return None
    return {
        "analysis_schema_version": schema_version,
        "description_sha256": description_sha256,
        "lane": lane,
        "status": status,
        "supplied_image_count": supplied_image_count,
        "visual_analysis_call_count": call_count,
    }


def parse_reply_visual_description_attempt(
    event: Mapping[str, Any], record: LogRecord
) -> tuple[str | None, dict[str, Any] | None]:
    """Validate the registered visual event and return only safe metadata."""
    raw_target_id = event.get("target_id")
    target_id = (
        raw_target_id.strip()
        if isinstance(raw_target_id, str) and raw_target_id.strip()
        else None
    )
    if (
        event.get("event") != "reply_visual_description"
        or set(event) - REPLY_VISUAL_DESCRIPTION_EVENT_FIELDS
        or target_id is None
    ):
        return target_id, None
    if "analysis" in event and (
        event.get("status") != "analysed"
        or not isinstance(event.get("analysis"), dict)
    ):
        return target_id, None
    metadata = _validated_reply_visual_description_metadata(
        event,
        normalise_lane_value=True,
        allow_empty_hash=True,
    )
    if metadata is None:
        return target_id, None
    return (
        target_id,
        {
            **metadata,
            "observed_at": record.timestamp,
            "record_fingerprint": record.record_fingerprint,
        },
    )


def normalise_account_lane(value: Any) -> str:
    """Map account-publication lane labels to canonical internal names."""
    lane = str(value or "").strip().casefold().replace("-", "_")
    if lane in {"quote_image", "daily_meme", "historical_context_reply"}:
        return lane
    return "other_account_post"


def _pseudonym(key: bytes, namespace: str, raw_value: str) -> str:
    digest = hmac.new(
        key,
        f"{namespace}\0{raw_value}".encode("utf-8", "surrogatepass"),
        hashlib.sha256,
    ).hexdigest()
    return f"{namespace}-{digest}"


def _open_regular_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    effective = flags
    if hasattr(os, "O_CLOEXEC"):
        effective |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        effective |= os.O_NOFOLLOW
    return os.open(path, effective, mode)


def load_or_create_pseudonym_key(root: Path) -> bytes:
    """Load the private pseudonym key, creating it atomically if absent."""
    path = root / "state" / "pseudonym-key"
    if not path.exists() and not path.is_symlink():
        data = secrets.token_bytes(32)
        try:
            descriptor = _open_regular_nofollow(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            pass
        except OSError as exc:
            raise ExtractorError(f"cannot create pseudonym key {path}: {exc}") from exc
        else:
            try:
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _fsync_directory(path.parent)
    try:
        descriptor = _open_regular_nofollow(path, os.O_RDONLY)
    except OSError as exc:
        raise ExtractorError(f"cannot open pseudonym key {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExtractorError("pseudonym key is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ExtractorError("pseudonym key permissions must be exactly 0600")
        data = os.read(descriptor, 33)
    finally:
        os.close(descriptor)
    if len(data) != 32:
        raise ExtractorError("pseudonym key must contain exactly 32 bytes")
    return data


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def decode_x_snowflake_time(
    post_id: Any,
    *,
    first_observed_at: Any = None,
) -> tuple[datetime | None, str | None]:
    """Decode one plausible X post ID without accepting arbitrary integers."""
    candidate = str(post_id or "").strip()
    if (
        not candidate.isdecimal()
        or not X_SNOWFLAKE_MIN_DIGITS <= len(candidate) <= X_SNOWFLAKE_MAX_DIGITS
    ):
        return None, "x_snowflake_id_invalid"
    try:
        value = int(candidate)
        timestamp_ms = (value >> 22) + X_SNOWFLAKE_EPOCH_MS
        if timestamp_ms < X_SNOWFLAKE_EPOCH_MS:
            return None, "x_snowflake_before_epoch"
        decoded = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None, "x_snowflake_timestamp_unrepresentable"
    observed = parse_optional_timestamp(first_observed_at)
    if observed is not None and decoded > observed + X_SNOWFLAKE_FUTURE_SKEW:
        return None, "x_snowflake_materially_after_first_observation"
    return decoded, None


def _set_snowflake_creation_time(post: dict[str, Any]) -> None:
    if post.get("creation_time_source") == "structured_event":
        return
    decoded, warning = decode_x_snowflake_time(
        post.get("post_id"), first_observed_at=post.get("first_observed_at")
    )
    if decoded is None:
        if post.get("creation_time_source") == "x_snowflake":
            post["created_at"] = None
            post["creation_time_confidence"] = "unavailable"
            post["creation_time_source"] = "unavailable"
        if warning:
            _record_warning(post, warning)
        return
    post["created_at"] = format_utc(decoded)
    post["creation_time_confidence"] = "high"
    post["creation_time_source"] = "x_snowflake"
    post["creation_time_provenance"] = [
        {
            "post_id_field": "post_id",
            "source": "x_snowflake",
        }
    ]


def _creation_time_observation(
    *,
    created_at: str,
    event: Mapping[str, Any],
    field: str,
    record: LogRecord,
) -> dict[str, str]:
    return {
        "created_at": created_at,
        "event_kind": str(event.get("event") or event.get("kind") or ""),
        "field": field,
        "observed_at": record.timestamp,
        "record_fingerprint": record.record_fingerprint,
        "source": "structured_event",
    }


def _snowflake_creation_observation(post: Mapping[str, Any], value: str) -> dict[str, str]:
    return {
        "created_at": value,
        "post_id_field": "post_id",
        "source": "x_snowflake",
    }


def _record_creation_time_conflict(
    post: dict[str, Any],
    observations: Iterable[Mapping[str, Any]],
) -> None:
    post["creation_time_conflict"] = True
    post["creation_time_conflicts"] = _merge_unique_objects(
        list(post.get("creation_time_conflicts") or []),
        (dict(observation) for observation in observations),
    )
    _record_warning(post, "creation_time_conflict")


def _resolve_conflicted_creation_time(post: dict[str, Any]) -> None:
    decoded, warning = decode_x_snowflake_time(
        post.get("post_id"), first_observed_at=post.get("first_observed_at")
    )
    if decoded is None:
        post["created_at"] = None
        post["creation_time_confidence"] = "unavailable"
        post["creation_time_source"] = "unavailable"
        post["creation_time_provenance"] = []
        if warning:
            _record_warning(post, warning)
        return
    decoded_text = str(format_utc(decoded))
    post["created_at"] = decoded_text
    post["creation_time_confidence"] = "high"
    post["creation_time_source"] = "x_snowflake"
    snowflake_observation = _snowflake_creation_observation(post, decoded_text)
    post["creation_time_provenance"] = [snowflake_observation]
    post["creation_time_conflicts"] = _merge_unique_objects(
        list(post.get("creation_time_conflicts") or []),
        [snowflake_observation],
    )


def _set_explicit_creation_time(
    post: dict[str, Any],
    event: Mapping[str, Any],
    fields: Sequence[str],
    record: LogRecord,
    *,
    subject: str,
) -> None:
    supplied = sorted(
        (
            (field, str(event[field]).strip())
            for field in fields
            if event.get(field) is not None and str(event[field]).strip()
        ),
        key=lambda item: (item[1], item[0]),
    )
    if not supplied:
        _set_snowflake_creation_time(post)
        return
    first_observed = parse_optional_timestamp(post.get("first_observed_at"))
    valid: list[tuple[datetime, dict[str, str]]] = []
    for field, value in supplied:
        try:
            parsed = parse_aware_timestamp(value, option=f"structured {field}")
        except ExtractorError:
            _record_warning(post, f"invalid_explicit_{subject}_creation_time")
            continue
        if (
            first_observed is not None
            and parsed > first_observed + X_SNOWFLAKE_FUTURE_SKEW
        ):
            _record_warning(post, f"implausible_explicit_{subject}_creation_time")
            continue
        created_at = str(format_utc(parsed))
        valid.append(
            (
                parsed,
                _creation_time_observation(
                    created_at=created_at,
                    event=event,
                    field=field,
                    record=record,
                ),
            )
        )
    if not valid:
        _set_snowflake_creation_time(post)
        return

    for parsed, observation in valid:
        existing = parse_optional_timestamp(post.get("created_at"))
        existing_source = str(post.get("creation_time_source") or "unavailable")
        if post.get("creation_time_conflict"):
            _record_creation_time_conflict(post, [observation])
            _resolve_conflicted_creation_time(post)
            continue
        if existing is None or existing_source == "unavailable":
            post["created_at"] = observation["created_at"]
            post["creation_time_confidence"] = "high"
            post["creation_time_source"] = "structured_event"
            post["creation_time_provenance"] = _merge_unique_objects(
                list(post.get("creation_time_provenance") or []), [observation]
            )
            continue
        if abs((parsed - existing).total_seconds()) <= 1:
            post["creation_time_provenance"] = _merge_unique_objects(
                list(post.get("creation_time_provenance") or []), [observation]
            )
            if existing_source == "x_snowflake":
                post["creation_time_source"] = "structured_event"
            continue

        existing_observations = list(post.get("creation_time_provenance") or [])
        if existing_source == "structured_event":
            existing_observations = [
                {
                    **dict(value),
                    "created_at": str(value.get("created_at") or post.get("created_at")),
                }
                for value in existing_observations
                if isinstance(value, dict)
            ]
        elif existing_source == "x_snowflake":
            existing_observations = [
                _snowflake_creation_observation(post, str(post["created_at"]))
            ]
        _record_creation_time_conflict(
            post, [*existing_observations, observation]
        )
        _resolve_conflicted_creation_time(post)


def _blank_post(post_id: str, key: bytes, *, author_role: str = "user") -> dict[str, Any]:
    namespace = "account" if author_role == "account" else "unknown"
    raw_identity = "mrsMThatcher-account" if author_role == "account" else post_id
    return {
        "account_turn_asked_for_clarification": False,
        "account_content_ambiguity_authority": None,
        "account_content_conflict_other_count": 0,
        "account_content_conflicts": [],
        "account_graph_ambiguity_authority": None,
        "account_graph_ambiguous_fields": [],
        "account_graph_conflict_other_count": 0,
        "account_graph_conflicts": [],
        "account_graph_kind": None,
        "author_key": _pseudonym(key, namespace, raw_identity),
        "author_role": author_role,
        "canonical_event_id": stable_id("post", post_id),
        "conversation_id": None,
        "content_evidence_authority": None,
        "created_at": None,
        "creation_time_conflict": False,
        "creation_time_conflicts": [],
        "creation_time_confidence": "unavailable",
        "creation_time_provenance": [],
        "creation_time_source": "unavailable",
        "derivation_parser_version": PARSER_VERSION,
        "first_observed_at": None,
        "generated_reply_text": None,
        "graph_evidence_authority": None,
        "graph_field_evidence_authorities": {},
        "lane": "other conversational lane",
        "last_observed_at": None,
        "parent_observation_status": "unavailable",
        "parent_post_id": None,
        "pipeline_stage_summaries": [],
        "post_id": post_id,
        "publication_authority": None,
        "publication_evidence": [],
        "publication_status": "unavailable" if author_role == "account" else "observed",
        "reconstruction_confidence": "low",
        "reply_media_context_observations": [],
        "reply_media_context_other_count": 0,
        "reply_requirement": None,
        "reply_visual_context_summary": {
            "analysis_attempt_count": 0,
            "analysis_history_complete": True,
            "analysis_observation_status": "not_applicable",
            "analysis_schema_versions": [],
            "collection_history_complete": True,
            "distinct_successful_description_count": 0,
            "latest_analysis_status": None,
            "latest_collection_status": None,
            "native_photo_count_max": 0,
            "omitted_media_observation_count": 0,
            "omitted_visual_event_count": 0,
            "successful_analysis_count": 0,
            "successful_description_sha256s": [],
            "visual_event_count": 0,
        },
        "reply_visual_description_attempts": [],
        "reply_visual_description_other_count": 0,
        "root_post_id": None,
        "route_source": None,
        "schema_version": SCHEMA_VERSION,
        "send_attempts": [],
        "self_observation_count": 0,
        "self_observation_last_observed_at": None,
        "source_provenance": [],
        "tested_pipeline_stage_summaries": [],
        "text": None,
        "text_source": "unavailable",
        "thread_id": None,
        "trusted_fact_count": None,
        "trusted_fact_ids": [],
        "public_text": None,
        "visible_media_text": None,
        "warnings": [],
    }


def _confidence_rank(value: Any) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(value), 0)


def _merge_unique_objects(existing: list[Any], additions: Iterable[Any]) -> list[Any]:
    keyed = {canonical_json_bytes(item, newline=False): item for item in existing}
    for item in additions:
        keyed.setdefault(canonical_json_bytes(item, newline=False), item)
    return [keyed[key] for key in sorted(keyed)]


def _reply_observation_order_key(
    observation: Mapping[str, Any],
) -> tuple[str, str, bytes]:
    return (
        str(observation.get("observed_at") or ""),
        str(observation.get("record_fingerprint") or ""),
        canonical_json_bytes(dict(observation), newline=False),
    )


def _merge_bounded_reply_observations(
    post: dict[str, Any],
    *,
    field: str,
    other_count_field: str,
    additions: Iterable[Mapping[str, Any]],
    maximum: int,
) -> None:
    """Deduplicate by source record and retain the newest deterministic set."""
    prior_other_count = post.get(other_count_field)
    if type(prior_other_count) is not int or prior_other_count < 0:
        prior_other_count = 0
    by_fingerprint: dict[str, dict[str, Any]] = {}
    existing = [
        item for item in post.get(field) or [] if isinstance(item, dict)
    ]
    addition_rows = [dict(item) for item in additions]
    existing_fingerprints = {
        str(item.get("record_fingerprint") or "")
        for item in existing
        if item.get("record_fingerprint")
    }
    previously_seen_fingerprints = {
        str(item.get("record_fingerprint") or "")
        for item in post.get("source_provenance") or []
        if isinstance(item, dict) and item.get("record_fingerprint")
    }
    new_fingerprints = {
        str(item.get("record_fingerprint") or "")
        for item in addition_rows
        if item.get("record_fingerprint")
    } - existing_fingerprints - previously_seen_fingerprints
    for raw in [*existing, *addition_rows]:
        item = dict(raw)
        fingerprint = str(item.get("record_fingerprint") or "")
        if not fingerprint:
            continue
        current = by_fingerprint.get(fingerprint)
        if current is None or canonical_json_bytes(
            item, newline=False
        ) < canonical_json_bytes(current, newline=False):
            by_fingerprint[fingerprint] = item
    ordered = sorted(by_fingerprint.values(), key=_reply_observation_order_key)
    post[field] = ordered[-maximum:] if maximum else []
    total_observation_count = (
        prior_other_count + len(existing_fingerprints) + len(new_fingerprints)
    )
    post[other_count_field] = max(
        0, total_observation_count - len(post[field])
    )


def _derive_reply_visual_context_summary(
    post: Mapping[str, Any],
) -> dict[str, Any]:
    media = sorted(
        (
            dict(item)
            for item in post.get("reply_media_context_observations") or []
            if isinstance(item, dict)
        ),
        key=_reply_observation_order_key,
    )
    visual_events = sorted(
        (
            dict(item)
            for item in post.get("reply_visual_description_attempts") or []
            if isinstance(item, dict)
        ),
        key=_reply_observation_order_key,
    )
    successful = [
        item for item in visual_events if item.get("status") == "analysed"
    ]
    successful_hashes = sorted(
        {
            str(item["description_sha256"])
            for item in successful
            if isinstance(item.get("description_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(item["description_sha256"]))
        }
    )
    supplied_observed = any(
        item.get("status") == "supplied"
        and type(item.get("photo_count")) is int
        and item["photo_count"] > 0
        for item in media
    )
    native_counts = [
        int(item["photo_count"])
        for item in media
        if type(item.get("photo_count")) is int and item["photo_count"] >= 0
    ] + [
        int(item["supplied_image_count"])
        for item in visual_events
        if type(item.get("supplied_image_count")) is int
        and item["supplied_image_count"] >= 0
    ]
    omitted_media_observation_count = post.get(
        "reply_media_context_other_count"
    )
    if (
        type(omitted_media_observation_count) is not int
        or omitted_media_observation_count < 0
    ):
        omitted_media_observation_count = 0
    omitted_visual_event_count = post.get(
        "reply_visual_description_other_count"
    )
    if (
        type(omitted_visual_event_count) is not int
        or omitted_visual_event_count < 0
    ):
        omitted_visual_event_count = 0
    analysis_attempt_count = sum(
        int(item["visual_analysis_call_count"])
        for item in visual_events
        if type(item.get("visual_analysis_call_count")) is int
        and item["visual_analysis_call_count"] >= 0
    )
    visual_event_count = len(visual_events)
    if successful:
        observation_status = "analysed"
    elif omitted_visual_event_count > 0:
        observation_status = "history_incomplete"
    elif analysis_attempt_count > 0:
        observation_status = "attempted_not_analysed"
    elif visual_event_count > 0:
        observation_status = "not_attempted"
    elif supplied_observed:
        observation_status = "not_observed"
    else:
        observation_status = "not_applicable"
    return {
        "analysis_attempt_count": analysis_attempt_count,
        "analysis_history_complete": omitted_visual_event_count == 0,
        "analysis_observation_status": observation_status,
        "analysis_schema_versions": sorted(
            {
                int(item["analysis_schema_version"])
                for item in visual_events
                if type(item.get("analysis_schema_version")) is int
                and item["analysis_schema_version"] > 0
            }
        ),
        "collection_history_complete": omitted_media_observation_count == 0,
        "distinct_successful_description_count": len(successful_hashes),
        "latest_analysis_status": (
            str(visual_events[-1].get("status")) if visual_events else None
        ),
        "latest_collection_status": (
            str(media[-1].get("status")) if media else None
        ),
        "native_photo_count_max": max(native_counts, default=0),
        "omitted_media_observation_count": omitted_media_observation_count,
        "omitted_visual_event_count": omitted_visual_event_count,
        "successful_analysis_count": len(successful),
        "successful_description_sha256s": successful_hashes,
        "visual_event_count": visual_event_count,
    }


def _record_warning(post: dict[str, Any], warning: str) -> None:
    post["warnings"] = sorted(set([*post.get("warnings", []), warning]))


def _touch_post(post: dict[str, Any], record: LogRecord) -> None:
    post["source_provenance"] = _merge_unique_objects(
        list(post.get("source_provenance") or []), [record.provenance()]
    )
    if (
        post.get("first_observed_at") is None
        or record.timestamp < str(post["first_observed_at"])
    ):
        post["first_observed_at"] = record.timestamp
    if (
        post.get("last_observed_at") is None
        or record.timestamp > str(post["last_observed_at"])
    ):
        post["last_observed_at"] = record.timestamp
    for warning in record.warnings:
        _record_warning(post, warning)
    _set_snowflake_creation_time(post)


TEXT_SOURCE_RANK = {
    "unavailable": 0,
    "mention_observation": 10,
    "image_summary": 30,
    "image_quote_text": 40,
    "public_text": 50,
    "historical_context_reply": 50,
}


def _set_text(
    post: dict[str, Any],
    text: Any,
    *,
    source: str = "mention_observation",
    public_text: bool = True,
    visible_media_text: bool = False,
) -> None:
    """Set the best source-faithful visible review text by explicit provenance."""
    candidate = str(text or "")
    if not candidate:
        return
    current = str(post.get("text") or "")
    if current and current != candidate:
        _record_warning(post, "conflicting_text_observations")
    current_source = str(post.get("text_source") or "unavailable")
    candidate_rank = TEXT_SOURCE_RANK.get(source, 0)
    current_rank = TEXT_SOURCE_RANK.get(current_source, 0)
    if (
        not current
        or candidate_rank > current_rank
        or (candidate_rank == current_rank and len(candidate) > len(current))
    ):
        post["text"] = candidate
        post["text_source"] = source
    if public_text:
        current_public = str(post.get("public_text") or "")
        if not current_public or len(candidate) > len(current_public):
            post["public_text"] = candidate
    if visible_media_text:
        current_media = str(post.get("visible_media_text") or "")
        if not current_media or len(candidate) > len(current_media):
            post["visible_media_text"] = candidate


def _account_authority_rank(value: Any) -> int:
    return ACCOUNT_EVIDENCE_AUTHORITY_RANK.get(str(value or ""), -1)


def _raise_reconstruction_confidence(post: dict[str, Any], value: str) -> None:
    if _confidence_rank(value) > _confidence_rank(post.get("reconstruction_confidence")):
        post["reconstruction_confidence"] = value


def _remove_warnings(post: dict[str, Any], warnings: Iterable[str]) -> None:
    removed = set(warnings)
    post["warnings"] = sorted(
        warning
        for warning in set(post.get("warnings") or [])
        if warning not in removed
    )


def _bounded_conflict_text(value: Any) -> dict[str, Any] | None:
    if value is None or str(value) == "":
        return None
    text = str(value)
    return {
        "excerpt": text[:MAX_ACCOUNT_CONFLICT_TEXT_CHARS],
        "length": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "truncated": len(text) > MAX_ACCOUNT_CONFLICT_TEXT_CHARS,
    }


def _merge_bounded_account_conflicts(
    post: dict[str, Any],
    field: str,
    additions: Iterable[Mapping[str, Any]],
) -> None:
    existing = [
        copy.deepcopy(dict(value))
        for value in post.get(field) or []
        if isinstance(value, dict)
    ]
    merged = _merge_unique_objects(existing, additions)
    overflow_field = f"{field[:-1]}_other_count"
    previous_overflow = int(post.get(overflow_field) or 0)
    post[field] = merged[:MAX_ACCOUNT_EVIDENCE_CONFLICTS]
    post[overflow_field] = previous_overflow + max(
        0, len(merged) - MAX_ACCOUNT_EVIDENCE_CONFLICTS
    )


def _content_conflict_entry(
    candidate: Mapping[str, Any],
    *,
    authority: str,
    disposition: str,
) -> dict[str, Any]:
    return {
        "authority": authority,
        "disposition": disposition,
        "public_text": _bounded_conflict_text(candidate.get("public_text")),
        "text": _bounded_conflict_text(candidate.get("text")),
        "text_source": str(candidate.get("text_source") or "unavailable"),
        "visible_media_text": _bounded_conflict_text(
            candidate.get("visible_media_text")
        ),
    }


def _graph_conflict_entry(
    candidate: Mapping[str, Any],
    *,
    authority: str,
    disposition: str,
) -> dict[str, Any]:
    return {
        "account_graph_kind": candidate.get("account_graph_kind"),
        "authority": authority,
        "conversation_id": candidate.get("conversation_id"),
        "disposition": disposition,
        "lane": candidate.get("lane"),
        "parent_observation_status": candidate.get("parent_observation_status"),
        "parent_post_id": candidate.get("parent_post_id"),
        "quote_id": candidate.get("quote_id"),
        "root_post_id": candidate.get("root_post_id"),
    }


def _normalise_account_content_candidate(
    *,
    text: Any,
    text_source: str,
    public_text: Any,
    visible_media_text: Any,
) -> dict[str, Any]:
    selected_value = str(text or "")
    public_value = str(public_text or "")
    media_value = str(visible_media_text or "")
    selected = selected_value if selected_value.strip() else None
    return {
        "public_text": public_value if public_value.strip() else None,
        "text": selected,
        "text_source": text_source if selected else "unavailable",
        "visible_media_text": media_value if media_value.strip() else None,
    }


def _current_account_content(post: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: copy.deepcopy(post.get(field)) for field in ACCOUNT_CONTENT_FIELDS
    }


def _assign_account_content(
    post: dict[str, Any], candidate: Mapping[str, Any], authority: str
) -> None:
    for field in ACCOUNT_CONTENT_FIELDS:
        post[field] = copy.deepcopy(candidate.get(field))
    post["content_evidence_authority"] = authority
    post["account_content_ambiguity_authority"] = None
    _remove_warnings(post, ACCOUNT_UNAVAILABLE_TEXT_WARNINGS)
    _raise_reconstruction_confidence(post, "high")


def _record_content_conflict(
    post: dict[str, Any],
    candidate: Mapping[str, Any],
    *,
    authority: str,
    disposition: str,
) -> None:
    _merge_bounded_account_conflicts(
        post,
        "account_content_conflicts",
        (
            _content_conflict_entry(
                candidate,
                authority=authority,
                disposition=disposition,
            ),
        ),
    )
    _record_warning(post, "account_content_conflict")


def _merge_account_content_evidence(
    post: dict[str, Any],
    candidate: Mapping[str, Any],
    *,
    authority: str,
) -> None:
    """Merge one coherent account visible-content candidate by authority."""
    incoming = _normalise_account_content_candidate(
        text=candidate.get("text"),
        text_source=str(candidate.get("text_source") or "unavailable"),
        public_text=candidate.get("public_text"),
        visible_media_text=candidate.get("visible_media_text"),
    )
    incoming_text = incoming.get("text")
    if not incoming_text:
        return

    incoming_rank = _account_authority_rank(authority)
    ambiguity_authority = str(
        post.get("account_content_ambiguity_authority") or ""
    )
    if ambiguity_authority:
        ambiguity_rank = _account_authority_rank(ambiguity_authority)
        if incoming_rank > ambiguity_rank:
            _assign_account_content(post, incoming, authority)
        else:
            disposition = (
                "equal_authority_ambiguous"
                if incoming_rank == ambiguity_rank
                else "rejected_lower_priority"
            )
            _record_content_conflict(
                post,
                incoming,
                authority=authority,
                disposition=disposition,
            )
            if incoming_rank == ambiguity_rank:
                _record_warning(post, "equal_authority_account_content_conflict")
            else:
                _record_warning(post, "lower_priority_account_content_conflict")
        return

    current = _current_account_content(post)
    current_text = str(current.get("text") or "") or None
    if current_text is None:
        _assign_account_content(post, incoming, authority)
        return

    current_authority = str(post.get("content_evidence_authority") or "")
    if not current_authority:
        current_authority = str(post.get("publication_authority") or "")
    current_rank = _account_authority_rank(current_authority)

    if current_text == incoming_text:
        if incoming_rank > current_rank:
            _assign_account_content(post, incoming, authority)
            return
        if incoming_rank < current_rank:
            return
        current_source = str(current.get("text_source") or "unavailable")
        incoming_source = str(incoming.get("text_source") or "unavailable")
        if TEXT_SOURCE_RANK.get(incoming_source, 0) > TEXT_SOURCE_RANK.get(
            current_source, 0
        ):
            _assign_account_content(post, incoming, authority)
            return
        if TEXT_SOURCE_RANK.get(incoming_source, 0) < TEXT_SOURCE_RANK.get(
            current_source, 0
        ):
            return
        for field in ("public_text", "visible_media_text"):
            if post.get(field) is None and incoming.get(field) is not None:
                post[field] = incoming[field]
        post["content_evidence_authority"] = authority
        _remove_warnings(post, ACCOUNT_UNAVAILABLE_TEXT_WARNINGS)
        _raise_reconstruction_confidence(post, "high")
        return

    if incoming_rank > current_rank:
        _record_content_conflict(
            post,
            current,
            authority=current_authority,
            disposition="rejected_lower_priority",
        )
        _record_warning(post, "lower_priority_account_content_conflict")
        _assign_account_content(post, incoming, authority)
        return
    if incoming_rank < current_rank:
        _record_content_conflict(
            post,
            incoming,
            authority=authority,
            disposition="rejected_lower_priority",
        )
        _record_warning(post, "lower_priority_account_content_conflict")
        return

    current_source_rank = TEXT_SOURCE_RANK.get(
        str(current.get("text_source") or "unavailable"), 0
    )
    incoming_source_rank = TEXT_SOURCE_RANK.get(
        str(incoming.get("text_source") or "unavailable"), 0
    )
    if incoming_source_rank != current_source_rank:
        if incoming_source_rank > current_source_rank:
            rejected = current
            rejected_authority = current_authority
            selected = incoming
            selected_authority = authority
        else:
            rejected = incoming
            rejected_authority = authority
            selected = current
            selected_authority = current_authority
        _record_content_conflict(
            post,
            rejected,
            authority=rejected_authority,
            disposition="rejected_equal_authority_source_preference",
        )
        _record_warning(post, "equal_authority_account_content_conflict")
        _assign_account_content(post, selected, selected_authority)
        return

    _record_content_conflict(
        post,
        current,
        authority=current_authority,
        disposition="equal_authority_ambiguous",
    )
    _record_content_conflict(
        post,
        incoming,
        authority=authority,
        disposition="equal_authority_ambiguous",
    )
    for field in ACCOUNT_CONTENT_FIELDS:
        post[field] = None if field != "text_source" else "unavailable"
    post["content_evidence_authority"] = None
    post["account_content_ambiguity_authority"] = authority
    post["reconstruction_confidence"] = "medium"
    _record_warning(post, "equal_authority_account_content_conflict")


def _current_account_graph(post: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "account_graph_kind": post.get("account_graph_kind"),
        **{field: copy.deepcopy(post.get(field)) for field in ACCOUNT_GRAPH_FIELDS},
    }


def _graph_disagreement_fields(
    current: Mapping[str, Any], incoming: Mapping[str, Any]
) -> list[str]:
    fields: list[str] = []
    if current.get("account_graph_kind") != incoming.get("account_graph_kind"):
        fields.append("account_graph_kind")
        fields.extend(
            field
            for field in ACCOUNT_GRAPH_FIELDS
            if current.get(field) != incoming.get(field)
        )
        return sorted(set(fields))
    for field in ACCOUNT_GRAPH_FIELDS:
        existing = current.get(field)
        candidate = incoming.get(field)
        if existing is None or candidate is None:
            continue
        if field == "lane" and existing == "other conversational lane":
            continue
        if existing != candidate:
            fields.append(field)
    return sorted(set(fields))


def _assign_account_graph(
    post: dict[str, Any], candidate: Mapping[str, Any], authority: str
) -> None:
    post["account_graph_kind"] = candidate.get("account_graph_kind")
    field_authorities: dict[str, str] = {}
    for field in ACCOUNT_GRAPH_FIELDS:
        post[field] = copy.deepcopy(candidate.get(field))
        if candidate.get(field) is not None or (
            field == "parent_post_id"
            and candidate.get("account_graph_kind") == "root"
        ):
            field_authorities[field] = authority
    post["graph_evidence_authority"] = authority
    post["graph_field_evidence_authorities"] = field_authorities
    post["account_graph_ambiguity_authority"] = None
    post["account_graph_ambiguous_fields"] = []
    _raise_reconstruction_confidence(post, "medium")


def _record_graph_conflict(
    post: dict[str, Any],
    candidate: Mapping[str, Any],
    *,
    authority: str,
    disposition: str,
) -> None:
    _merge_bounded_account_conflicts(
        post,
        "account_graph_conflicts",
        (
            _graph_conflict_entry(
                candidate,
                authority=authority,
                disposition=disposition,
            ),
        ),
    )
    _record_warning(post, "account_graph_conflict")


def _fill_absent_account_graph_fields(
    post: dict[str, Any], candidate: Mapping[str, Any], authority: str
) -> None:
    if post.get("account_graph_kind") != candidate.get("account_graph_kind"):
        return
    field_authorities = dict(post.get("graph_field_evidence_authorities") or {})
    for field in ACCOUNT_GRAPH_FIELDS:
        if field == "parent_post_id" and post.get("account_graph_kind") == "root":
            continue
        existing = post.get(field)
        missing = existing is None or (
            field == "lane" and existing == "other conversational lane"
        )
        if missing and candidate.get(field) is not None:
            post[field] = copy.deepcopy(candidate[field])
            field_authorities[field] = authority
    post["graph_field_evidence_authorities"] = field_authorities


def _merge_account_graph_evidence(
    post: dict[str, Any],
    candidate: Mapping[str, Any],
    *,
    authority: str,
) -> None:
    """Merge account graph identity without lower-priority reparenting."""
    incoming = {
        "account_graph_kind": candidate.get("account_graph_kind"),
        **{field: copy.deepcopy(candidate.get(field)) for field in ACCOUNT_GRAPH_FIELDS},
    }
    incoming_rank = _account_authority_rank(authority)
    ambiguity_authority = str(post.get("account_graph_ambiguity_authority") or "")
    if ambiguity_authority:
        ambiguity_rank = _account_authority_rank(ambiguity_authority)
        if incoming_rank > ambiguity_rank:
            _assign_account_graph(post, incoming, authority)
        else:
            disposition = (
                "equal_authority_ambiguous"
                if incoming_rank == ambiguity_rank
                else "rejected_lower_priority"
            )
            _record_graph_conflict(
                post,
                incoming,
                authority=authority,
                disposition=disposition,
            )
            if incoming_rank == ambiguity_rank:
                _record_warning(post, "equal_authority_account_graph_conflict")
            else:
                _record_warning(post, "lower_priority_account_graph_conflict")
        return

    current_authority = str(post.get("graph_evidence_authority") or "")
    if not current_authority:
        _assign_account_graph(post, incoming, authority)
        return
    current = _current_account_graph(post)
    current_rank = _account_authority_rank(current_authority)
    disagreements = _graph_disagreement_fields(current, incoming)

    if incoming_rank > current_rank:
        if disagreements:
            _record_graph_conflict(
                post,
                current,
                authority=current_authority,
                disposition="rejected_lower_priority",
            )
            _record_warning(post, "lower_priority_account_graph_conflict")
        same_kind = (
            current.get("account_graph_kind") == incoming.get("account_graph_kind")
        )
        _assign_account_graph(post, incoming, authority)
        if same_kind:
            _fill_absent_account_graph_fields(post, current, current_authority)
        return
    if incoming_rank < current_rank:
        if disagreements:
            _record_graph_conflict(
                post,
                incoming,
                authority=authority,
                disposition="rejected_lower_priority",
            )
            _record_warning(post, "lower_priority_account_graph_conflict")
        _fill_absent_account_graph_fields(post, incoming, authority)
        return
    if not disagreements:
        _fill_absent_account_graph_fields(post, incoming, authority)
        return

    _record_graph_conflict(
        post,
        current,
        authority=current_authority,
        disposition="equal_authority_ambiguous",
    )
    _record_graph_conflict(
        post,
        incoming,
        authority=authority,
        disposition="equal_authority_ambiguous",
    )
    post["graph_evidence_authority"] = authority
    post["account_graph_ambiguity_authority"] = authority
    post["account_graph_ambiguous_fields"] = disagreements
    field_authorities = dict(post.get("graph_field_evidence_authorities") or {})
    if "account_graph_kind" in disagreements:
        post["account_graph_kind"] = "ambiguous"
    for field in disagreements:
        if field == "account_graph_kind":
            continue
        if field == "lane":
            post[field] = "other conversational lane"
        elif field == "parent_observation_status":
            post[field] = "ambiguous"
        else:
            post[field] = None
        field_authorities.pop(field, None)
    if "parent_post_id" in disagreements:
        post["parent_observation_status"] = "ambiguous"
        field_authorities.pop("parent_observation_status", None)
    post["graph_field_evidence_authorities"] = field_authorities
    post["reconstruction_confidence"] = "low"
    _record_warning(post, "equal_authority_account_graph_conflict")


def _ensure_account_evidence_defaults(post: dict[str, Any]) -> None:
    post.setdefault("account_content_ambiguity_authority", None)
    post.setdefault("account_content_conflict_other_count", 0)
    post.setdefault("account_content_conflicts", [])
    post.setdefault("account_graph_ambiguity_authority", None)
    post.setdefault("account_graph_ambiguous_fields", [])
    post.setdefault("account_graph_conflict_other_count", 0)
    post.setdefault("account_graph_conflicts", [])
    post.setdefault("account_graph_kind", None)
    post.setdefault("content_evidence_authority", None)
    post.setdefault("graph_evidence_authority", None)
    post.setdefault("graph_field_evidence_authorities", {})
    if post.get("author_role") != "account":
        return
    publication_authority = str(post.get("publication_authority") or "")
    if publication_authority not in ACCOUNT_EVIDENCE_AUTHORITY_RANK:
        return
    lane = str(post.get("lane") or "")
    if lane in {"quote_image", "daily_meme"}:
        post["account_graph_kind"] = post.get("account_graph_kind") or "root"
    elif lane == "historical_context_reply":
        post["account_graph_kind"] = post.get("account_graph_kind") or "reply"
    else:
        return
    if not post.get("graph_evidence_authority"):
        post["graph_evidence_authority"] = publication_authority
    if not post.get("graph_field_evidence_authorities"):
        post["graph_field_evidence_authorities"] = {
            field: publication_authority
            for field in ACCOUNT_GRAPH_FIELDS
            if post.get(field) is not None
            or (field == "parent_post_id" and post["account_graph_kind"] == "root")
        }
    if post.get("text") and not post.get("content_evidence_authority"):
        post["content_evidence_authority"] = publication_authority


def _set_author(post: dict[str, Any], key: bytes, raw_author_id: Any) -> None:
    if post.get("author_role") == "account":
        return
    raw = str(raw_author_id or "")
    if not raw:
        return
    candidate = _pseudonym(key, "user", raw)
    current = str(post.get("author_key") or "")
    if current.startswith("user-") and current != candidate:
        _record_warning(post, "conflicting_pseudonymous_author_observations")
        return
    post["author_key"] = candidate


def _record_account_self_observation(
    post: dict[str, Any], record: LogRecord
) -> None:
    post["self_observation_count"] = min(
        2_147_483_647,
        int(post.get("self_observation_count") or 0) + 1,
    )
    current = str(post.get("self_observation_last_observed_at") or "")
    if not current or record.timestamp > current:
        post["self_observation_last_observed_at"] = record.timestamp
    _record_warning(post, "account_post_observed_by_mention_poll")


def _set_identity(post: dict[str, Any], field: str, value: Any) -> None:
    candidate = str(value or "").strip()
    if not candidate or candidate.casefold() in {"none", "null", "unavailable"}:
        return
    current = post.get(field)
    if current and str(current) != candidate:
        _record_warning(post, f"conflicting_{field}")
        return
    post[field] = candidate


PIPELINE_EVENT_KINDS = frozenset(
    {
        "single_call_reply_decision",
        "ai_reply_pipeline_decision",
        "ai_reply_pipeline_effective_outcome",
        "ai_reply_pipeline_failure",
        "ai_reply_pipeline_outcome",
        "ai_reply_pipeline_stage_summary",
        "reply_strategy_decision",
        "reply_strategy_failure",
        "reply_strategy_local_rejection",
        "reply_strategy_outcome",
        "reply_strategy_rejection",
    }
)


@dataclass(frozen=True)
class StructuredEventContract:
    """Declare fields and publication semantics for a structured event."""

    target_fields: tuple[str, ...]
    incoming_text_fields: tuple[str, ...] = ()
    author_fields: tuple[str, ...] = ()
    identity_fields: tuple[str, ...] = ()
    parent_fields: tuple[str, ...] = ()
    target_creation_fields: tuple[str, ...] = ()
    reply_creation_fields: tuple[str, ...] = ()
    confirms_publication: bool = False


_PIPELINE_CONTRACT = StructuredEventContract(
    target_fields=("target_id",),
    incoming_text_fields=("incoming_text", "incoming_contribution"),
    author_fields=("author_id",),
    identity_fields=("conversation_id", "root_post_id", "thread_id"),
    parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
    target_creation_fields=(
        "target_created_at",
        "post_created_at",
        "tweet_created_at",
    ),
)

STRUCTURED_CONVERSATION_EVENT_FIELDS: dict[str, StructuredEventContract] = {
    kind: _PIPELINE_CONTRACT for kind in PIPELINE_EVENT_KINDS
}
STRUCTURED_CONVERSATION_EVENT_FIELDS.update(
    {
        "single_call_reply_posting_outcome": StructuredEventContract(
            target_fields=("target_id",),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "reply_posted": StructuredEventContract(
            target_fields=("target_id",),
            incoming_text_fields=("incoming_text", "incoming_contribution"),
            author_fields=("author_id",),
            identity_fields=("conversation_id", "root_post_id", "thread_id"),
            parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
            target_creation_fields=(
                "target_created_at",
                "post_created_at",
                "tweet_created_at",
            ),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "mention_reply_posted": StructuredEventContract(
            target_fields=("mention_id", "target_id"),
            incoming_text_fields=("incoming_text", "incoming_contribution"),
            author_fields=("author_id",),
            identity_fields=("conversation_id", "root_post_id", "thread_id"),
            parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
            target_creation_fields=("target_created_at", "tweet_created_at"),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "hot_post_reply_posted": StructuredEventContract(
            target_fields=("hot_post_reply_id", "target_id"),
            incoming_text_fields=("incoming_text", "incoming_contribution"),
            author_fields=("author_id",),
            identity_fields=("conversation_id", "root_post_id", "thread_id"),
            parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
            target_creation_fields=("target_created_at", "tweet_created_at"),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "quote_tweet_reply_posted": StructuredEventContract(
            target_fields=("quote_tweet_id", "target_id"),
            incoming_text_fields=("incoming_text", "incoming_contribution"),
            author_fields=("author_id",),
            identity_fields=("conversation_id", "root_post_id", "thread_id"),
            parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
            target_creation_fields=("target_created_at", "tweet_created_at"),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "clarification_reply_used": StructuredEventContract(
            target_fields=("target_id",),
            identity_fields=("conversation_id", "root_post_id", "thread_id"),
            parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
            target_creation_fields=("target_created_at",),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "repair_reply_completed": StructuredEventContract(
            target_fields=("target_id",),
            identity_fields=("conversation_id", "root_post_id", "thread_id"),
            parent_fields=("parent_post_id", "replied_to_post_id", "in_reply_to_status_id"),
            target_creation_fields=("target_created_at",),
            reply_creation_fields=("reply_created_at",),
            confirms_publication=True,
        ),
        "reply_visual_description": StructuredEventContract(
            target_fields=("target_id",),
        ),
    }
)

LEGACY_ACCOUNT_SEQUENCE_EVENT_KINDS = frozenset(
    {"main_post_posted", "historical_context_reply", "historical_context_obligation"}
)


def _registered_value(
    event: Mapping[str, Any], fields: Sequence[str]
) -> tuple[str, bool]:
    values = {
        str(event[field]).strip()
        for field in fields
        if event.get(field) is not None and str(event[field]).strip()
    }
    if len(values) > 1:
        return "", True
    return (next(iter(values)) if values else ""), False

PIPELINE_SUMMARY_FIELDS = (
    "claim_audit_outcomes",
    "claim_cleanup_called",
    "claim_risk_categories",
    "deterministic_reason",
    "deterministic_suppressed",
    "direct_answer_repair_attempted",
    "direct_answer_repair_outcome",
    "duplicate_repair_called",
    "duplicate_repair_outcome",
    "effective_reason",
    "effective_status",
    "evidence_confidence",
    "final_reply_kind",
    "final_validation",
    "mode",
    "model_call_count",
    "decision",
    "reply_kind",
    "reason_code",
    "outcome_type",
    "local_validation_status",
    "error_category",
    "failure_reason",
    "visible_turn_count",
    "visible_character_count",
    "same_author_interaction_count",
    "recent_conversational_reply_count",
    "recent_reply_count",
    "trusted_fact_count",
    "supplied_image_count",
    "model",
    "original_local_rejection_reason",
    "pipeline_stage_status",
    "reply_requirement",
    "reviewer_verdict",
    "revision_count",
    "route_source",
    "schema_invalid_stages",
    "status",
    "strategy_version",
    "terminal_reason",
    "tone",
    "trusted_fact_ids_supplied",
    "trusted_facts_supplied_count",
    "used_fact_count",
    "used_fact_ids",
)


def _pipeline_summary(kind: str, event: Mapping[str, Any], record: LogRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_id": record.record_fingerprint,
        "event_kind": kind,
        "observed_at": record.timestamp,
    }
    for field in PIPELINE_SUMMARY_FIELDS:
        if field in event:
            result[field] = copy.deepcopy(event[field])
    return result


def _looks_like_clarification_request(text: Any) -> bool:
    candidate = str(text or "").casefold()
    if "?" not in candidate:
        return False
    return bool(
        re.search(
            r"\b(could you clarify|can you clarify|what do you mean|which .* do you mean|"
            r"can you specify|could you specify|what exactly|which (?:part|point|claim))\b",
            candidate,
        )
        or re.search(
            r"\bwhich\s+(?:[a-z][a-z-]*\s+){0,3}"
            r"(?:measure|series|definition|source|period|law|statistic|metric|dataset|data)\b",
            candidate,
        )
        or re.search(
            r"\bwhat\s+(?:source|period|definition|measure|series|law|statistic|metric|dataset|data)\b",
            candidate,
        )
    )


_ATTEMPT_STATUS_RANK = {
    "started": 0,
    "remote_success_observed": 1,
    "failed": 2,
    "retired": 3,
    "confirmed": 4,
}


def _bounded_attempts(post: Mapping[str, Any]) -> list[dict[str, Any]]:
    attempts = [
        copy.deepcopy(dict(value))
        for value in post.get("send_attempts") or []
        if isinstance(value, dict) and value.get("transaction_id")
    ]
    attempts.sort(
        key=lambda value: (
            str(value.get("first_observed_at") or ""),
            str(value.get("transaction_id") or ""),
        )
    )
    return attempts[-MAX_SEND_ATTEMPTS_PER_TARGET:]


def _upsert_send_attempt(
    post: dict[str, Any],
    *,
    transaction_id: str,
    target_post_id: str,
    lane: str,
    text: str | None,
    record: LogRecord,
) -> None:
    attempts = _bounded_attempts(post)
    existing = next(
        (
            value
            for value in attempts
            if value.get("transaction_id") == transaction_id
        ),
        None,
    )
    if existing is None:
        existing = {
            "first_observed_at": record.timestamp,
            "lane": lane,
            "last_observed_status": "started",
            "status_observed_at": record.timestamp,
            "target_post_id": target_post_id,
            "text": text,
            "transaction_id": transaction_id,
        }
        attempts.append(existing)
    else:
        existing["first_observed_at"] = min(
            str(existing.get("first_observed_at") or record.timestamp),
            record.timestamp,
        )
        if text:
            existing["text"] = text
        if existing.get("last_observed_status") != "confirmed":
            existing["last_observed_status"] = "started"
            existing["status_observed_at"] = record.timestamp
    attempts.sort(
        key=lambda value: (
            str(value.get("first_observed_at") or ""),
            str(value.get("transaction_id") or ""),
        )
    )
    post["send_attempts"] = attempts[-MAX_SEND_ATTEMPTS_PER_TARGET:]


def _mark_send_attempt(
    post: dict[str, Any],
    *,
    record: LogRecord,
    status_value: str,
    transaction_id: str | None = None,
    reply_post_id: str | None = None,
    require_unambiguous: bool = False,
) -> dict[str, Any] | None:
    attempts = _bounded_attempts(post)
    matches = [
        value
        for value in attempts
        if transaction_id is None or value.get("transaction_id") == transaction_id
    ]
    if reply_post_id:
        exact = [
            value
            for value in matches
            if value.get("remote_post_id_observed") == reply_post_id
        ]
        if exact:
            matches = exact
    if require_unambiguous and len(matches) != 1:
        post["send_attempts"] = attempts
        return None
    if not matches:
        post["send_attempts"] = attempts
        return None
    selected = max(
        matches,
        key=lambda value: (
            str(value.get("first_observed_at") or ""),
            str(value.get("transaction_id") or ""),
        ),
    )
    existing_status = str(selected.get("last_observed_status") or "started")
    if (
        status_value == "confirmed"
        or _ATTEMPT_STATUS_RANK.get(status_value, 0)
        >= _ATTEMPT_STATUS_RANK.get(existing_status, 0)
    ):
        selected["last_observed_status"] = status_value
        selected["status_observed_at"] = record.timestamp
    if reply_post_id:
        selected["remote_post_id_observed"] = reply_post_id
    post["send_attempts"] = attempts[-MAX_SEND_ATTEMPTS_PER_TARGET:]
    return selected


def _bind_confirmed_send_attempt(
    post: dict[str, Any],
    *,
    record: LogRecord,
    reply_post_id: str,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Bind confirmation text only when attempt identity is sufficiently strong."""
    attempts = _bounded_attempts(post)
    target_post_id = str(post.get("post_id") or "")
    correctly_scoped = [
        attempt
        for attempt in attempts
        if attempt.get("target_post_id") == target_post_id
        and attempt.get("lane") == "conversational_reply"
    ]
    exact = [
        attempt
        for attempt in correctly_scoped
        if attempt.get("remote_post_id_observed") == reply_post_id
    ]
    warnings: list[str] = []
    if any(
        attempt.get("remote_post_id_observed")
        and attempt.get("remote_post_id_observed") != reply_post_id
        for attempt in correctly_scoped
    ):
        warnings.append("confirmed_reply_known_remote_id_mismatch")
    selected: dict[str, Any] | None = None
    if len(exact) == 1:
        selected = exact[0]
    elif len(exact) > 1:
        warnings.append("confirmed_reply_multiple_eligible_attempts")
        warnings.append("ambiguous_confirmed_reply_attempt_text")
    else:
        eligible = [
            attempt
            for attempt in correctly_scoped
            if attempt.get("last_observed_status") == "started"
            and not attempt.get("remote_post_id_observed")
        ]
        if len(eligible) == 1:
            selected = eligible[0]
        elif len(eligible) > 1:
            warnings.append("confirmed_reply_multiple_eligible_attempts")
            warnings.append("ambiguous_confirmed_reply_attempt_text")
        else:
            warnings.append("confirmed_reply_no_eligible_attempt_text")
            if any(
                attempt.get("last_observed_status") in {"failed", "retired"}
                for attempt in correctly_scoped
            ):
                warnings.append(
                    "confirmed_reply_failed_or_retired_attempt_text_not_reused"
                )
    if selected is not None:
        selected["last_observed_status"] = "confirmed"
        selected["status_observed_at"] = record.timestamp
        selected["remote_post_id_observed"] = reply_post_id
    post["send_attempts"] = attempts[-MAX_SEND_ATTEMPTS_PER_TARGET:]
    return selected, tuple(warnings)


@dataclass(frozen=True)
class LegacyAccountPublication:
    """Represent an account publication reconstructed from legacy logs."""

    post_id: str
    lane: str
    parent_post_id: str | None
    root_post_id: str
    conversation_id: str
    text: str | None
    text_source: str
    public_text: str | None
    visible_media_text: str | None
    quote_id: str | None
    evidence_kind: str
    evidence_records: tuple[LogRecord, ...]
    warnings: tuple[str, ...] = ()


def _valid_account_root_event(event: Mapping[str, Any]) -> bool:
    post_id = str(event.get("post_id") or "").strip()
    return bool(
        event.get("event_version") == 1
        and re.fullmatch(r"\d{1,30}", post_id)
        and str(event.get("root_post_id") or "") == post_id
        and str(event.get("conversation_id") or "") == post_id
        and normalise_account_lane(event.get("lane")) in {"quote_image", "daily_meme"}
        and event.get("publication_authority") == "confirmed_transport"
    )


def _valid_historical_context_event(event: Mapping[str, Any]) -> bool:
    parent_id = str(event.get("parent_post_id") or "").strip()
    reply_id = str(event.get("reply_post_id") or "").strip()
    return bool(
        event.get("event_version") == 1
        and re.fullmatch(r"\d{1,30}", parent_id)
        and re.fullmatch(r"\d{1,30}", reply_id)
        and str(event.get("root_post_id") or "") == parent_id
        and str(event.get("conversation_id") or "") == parent_id
        and normalise_account_lane(event.get("lane")) == "historical_context_reply"
        and event.get("publication_authority") == "confirmed_transport"
    )


def _authoritative_account_ids(
    records: Sequence[LogRecord],
    legacy_publications: Mapping[str, LegacyAccountPublication] | None = None,
) -> set[str]:
    result: set[str] = set()
    for record in records:
        event = record.structured_event
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event") or event.get("kind") or "")
        if kind == "account_root_posted" and _valid_account_root_event(event):
            result.add(str(event["post_id"]))
        elif (
            kind == "historical_context_reply_posted"
            and _valid_historical_context_event(event)
        ):
            result.add(str(event["reply_post_id"]))
    result.update(legacy_publications or _legacy_account_publications(records))
    return result


def _legacy_account_publications(
    records: Sequence[LogRecord],
) -> dict[str, LegacyAccountPublication]:
    """Recover only complete, unambiguous retained account-publication chains."""
    pending_main: dict[str, dict[str, Any]] = {}
    active_transport: dict[str, Any] | None = None
    last_remote_success: dict[str, Any] | None = None
    completed_context: dict[str, Any] | None = None
    publications: dict[str, LegacyAccountPublication] = {}

    for record in records:
        message = record.message
        match = MAIN_ATTEMPT_WRITTEN_RE.search(message)
        if match:
            lane = match.group(1)
            pending_main[lane] = {
                "attempt_id": match.group(2),
                "attempting": False,
                "records": [record],
            }

        match = MAIN_ATTEMPT_ATTEMPTING_RE.search(message)
        if match:
            lane, attempt_id = match.groups()
            state = pending_main.get(lane)
            if state is None or state.get("attempt_id") != attempt_id:
                pending_main.pop(lane, None)
            else:
                state["attempting"] = True
                state["records"].append(record)

        match = CREATE_ATTEMPT_RE.search(message)
        if match:
            lane = match.group(1)
            parent_id = match.group(3)
            active_transport = None
            last_remote_success = None
            completed_context = None
            text_value = decode_literal(match.group(4))
            if lane in {"quote_image", "daily_meme"}:
                state = pending_main.get(lane)
                if (
                    parent_id.casefold() in {"none", "null"}
                    and state is not None
                    and state.get("attempting") is True
                ):
                    active_transport = {
                        "kind": "main",
                        "lane": lane,
                        "transaction_id": match.group(2),
                        "text": text_value or None,
                        "record": record,
                        "main_state": state,
                    }
            elif (
                lane == "historical_context_reply"
                and re.fullmatch(r"\d{1,30}", parent_id)
            ):
                active_transport = {
                    "kind": "historical_context",
                    "lane": lane,
                    "transaction_id": match.group(2),
                    "parent_post_id": parent_id,
                    "text": text_value or None,
                    "record": record,
                }

        match = CREATED_ID_RE.search(message)
        if match:
            if active_transport is not None:
                last_remote_success = {
                    **active_transport,
                    "post_id": match.group(1),
                    "success_record": record,
                }
                if active_transport["kind"] == "main":
                    state = active_transport["main_state"]
                    state["transport"] = last_remote_success
                    state["records"].extend(
                        [active_transport["record"], record]
                    )
            active_transport = None

        match = MAIN_ATTEMPT_CONFIRMED_RE.search(message)
        if match:
            lane, attempt_id, post_id = match.groups()
            state = pending_main.get(lane)
            transport = state.get("transport") if state is not None else None
            if (
                state is None
                or state.get("attempt_id") != attempt_id
                or not isinstance(transport, dict)
                or transport.get("post_id") != post_id
            ):
                pending_main.pop(lane, None)
            else:
                state["confirmed_post_id"] = post_id
                state["records"].append(record)

        match = MAIN_SCHEDULE_FINALISED_RE.search(message)
        if match:
            lane, post_id = match.groups()
            state = pending_main.get(lane)
            if state is None or state.get("confirmed_post_id") != post_id:
                pending_main.pop(lane, None)
            else:
                state["finalised"] = True
                state["records"].append(record)

        event = record.structured_event
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event") or event.get("kind") or "")
        if kind == "main_post_posted":
            lane = str(event.get("lane") or "")
            post_id = str(event.get("post_id") or "")
            state = pending_main.get(lane)
            transport = state.get("transport") if state is not None else None
            if (
                lane in {"quote_image", "daily_meme"}
                and re.fullmatch(r"\d{1,30}", post_id)
                and state is not None
                and state.get("finalised") is True
                and state.get("confirmed_post_id") == post_id
                and isinstance(transport, dict)
                and transport.get("post_id") == post_id
            ):
                text = transport.get("text")
                warnings = () if text else ("legacy_account_root_text_unavailable",)
                publications[post_id] = LegacyAccountPublication(
                    post_id=post_id,
                    lane=normalise_account_lane(lane),
                    parent_post_id=None,
                    root_post_id=post_id,
                    conversation_id=post_id,
                    text=str(text) if text else None,
                    text_source="public_text" if text else "unavailable",
                    public_text=str(text) if text else None,
                    visible_media_text=(
                        str(text) if text and lane == "quote_image" else None
                    ),
                    quote_id=str(event.get("quote_hash") or "") or None,
                    evidence_kind="legacy_confirmed_main_post_sequence",
                    evidence_records=tuple([*state["records"], record]),
                    warnings=warnings,
                )
            pending_main.pop(lane, None)
            continue

        if kind == "historical_context_reply":
            parent_id = str(event.get("parent_post_id") or "")
            if (
                str(event.get("status") or "") in {"completed", "already_completed"}
                and isinstance(last_remote_success, dict)
                and last_remote_success.get("kind") == "historical_context"
                and last_remote_success.get("parent_post_id") == parent_id
            ):
                completed_context = {
                    **last_remote_success,
                    "quote_id": str(event.get("quote_id") or "") or None,
                    "completion_record": record,
                }
            else:
                completed_context = None
            continue

        if kind == "historical_context_obligation":
            parent_id = str(event.get("parent_post_id") or "")
            if (
                str(event.get("status") or "") in {"completed", "already_completed"}
                and str(event.get("context_reply_state") or "")
                == "context_reply_confirmed"
                and isinstance(completed_context, dict)
                and completed_context.get("parent_post_id") == parent_id
            ):
                reply_id = str(completed_context["post_id"])
                reply_text = completed_context.get("text")
                publications[reply_id] = LegacyAccountPublication(
                    post_id=reply_id,
                    lane="historical_context_reply",
                    parent_post_id=parent_id,
                    root_post_id=parent_id,
                    conversation_id=parent_id,
                    text=str(reply_text) if reply_text else None,
                    text_source=(
                        "historical_context_reply" if reply_text else "unavailable"
                    ),
                    public_text=str(reply_text) if reply_text else None,
                    visible_media_text=None,
                    quote_id=completed_context.get("quote_id"),
                    evidence_kind="legacy_confirmed_historical_context_sequence",
                    evidence_records=(
                        completed_context["record"],
                        completed_context["success_record"],
                        completed_context["completion_record"],
                        record,
                    ),
                    warnings=(
                        ()
                        if reply_text
                        else ("legacy_historical_context_text_unavailable",)
                    ),
                )
            completed_context = None
            last_remote_success = None

    return publications


def normalise_canonical_posts(
    records: Sequence[LogRecord],
    prior_posts: Sequence[Mapping[str, Any]],
    pseudonym_key: bytes,
    *,
    parser_statistics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Merge legacy and structured observations into stable canonical posts."""
    statistics = parser_statistics if parser_statistics is not None else {}
    for name in (
        "ignored_structured_event_count",
        "ignored_target_like_event_count",
        "ambiguous_registered_event_count",
        "registered_event_missing_target_count",
    ):
        statistics.setdefault(name, 0)
    statistics.setdefault("ignored_structured_event_kinds_by_kind", {})
    statistics.setdefault("ignored_target_like_event_kinds_by_kind", {})
    posts: dict[str, dict[str, Any]] = {
        str(row["post_id"]): copy.deepcopy(dict(row))
        for row in prior_posts
        if row.get("post_id")
    }
    for post in posts.values():
        post.setdefault("creation_time_conflict", False)
        post.setdefault("creation_time_conflicts", [])
        post.setdefault("public_text", None)
        post.setdefault("self_observation_count", 0)
        post.setdefault("self_observation_last_observed_at", None)
        post.setdefault("text_source", "unavailable")
        post.setdefault("visible_media_text", None)
        post.setdefault("reply_media_context_observations", [])
        post.setdefault("reply_media_context_other_count", 0)
        post.setdefault("reply_visual_description_attempts", [])
        post.setdefault("reply_visual_description_other_count", 0)
        post["reply_visual_context_summary"] = _derive_reply_visual_context_summary(
            post
        )
        _ensure_account_evidence_defaults(post)

    deduplicated: dict[str, LogRecord] = {}
    for record in records:
        deduplicated.setdefault(record.record_fingerprint, record)
    ordered_records = sorted(
        deduplicated.values(),
        key=lambda item: (
            item.timestamp,
            item.ordinal,
            item.record_fingerprint,
            item.source,
            item.line if item.line is not None else -1,
        ),
    )
    legacy_publications = _legacy_account_publications(ordered_records)
    authoritative_account_ids = _authoritative_account_ids(
        ordered_records, legacy_publications
    )
    active_attempt: tuple[str, str] | None = None

    def get_user(target_id: str) -> dict[str, Any]:
        account_proved = target_id in authoritative_account_ids
        row = posts.setdefault(
            target_id,
            _blank_post(
                target_id,
                pseudonym_key,
                author_role="account" if account_proved else "user",
            ),
        )
        if row.get("author_role") == "account" or account_proved:
            row["author_role"] = "account"
            row["author_key"] = _pseudonym(
                pseudonym_key, "account", "mrsMThatcher-account"
            )
        else:
            row["author_role"] = "user"
            row["publication_status"] = "observed"
        return row

    def claim_account_role(post_id: str) -> dict[str, Any]:
        row = posts.setdefault(
            post_id,
            _blank_post(post_id, pseudonym_key, author_role="account"),
        )
        row["author_role"] = "account"
        row["author_key"] = _pseudonym(
            pseudonym_key, "account", "mrsMThatcher-account"
        )
        _ensure_account_evidence_defaults(row)
        return row

    def record_publication_evidence(
        row: dict[str, Any],
        *,
        authority: str,
        evidence_kind: str,
        evidence_records: Sequence[LogRecord],
    ) -> None:
        existing_authority = str(row.get("publication_authority") or "")
        if (
            not existing_authority
            or authority == "structured_confirmation"
            or _account_authority_rank(authority)
            > _account_authority_rank(existing_authority)
        ):
            row["publication_authority"] = authority
        row["publication_status"] = "published"
        row["publication_evidence"] = _merge_unique_objects(
            list(row.get("publication_evidence") or []),
            (
                {
                    "authority": authority,
                    "event_kind": evidence_kind,
                    "observed_at": evidence_record.timestamp,
                    "record_fingerprint": evidence_record.record_fingerprint,
                }
                for evidence_record in evidence_records
            ),
        )
        for evidence_record in evidence_records:
            _touch_post(row, evidence_record)

    def publish_account_root(
        *,
        post_id: str,
        record: LogRecord,
        lane: str,
        text: str | None,
        text_source: str,
        public_text: str | None,
        visible_media_text: str | None,
        quote_id: str | None,
        authority: str,
        evidence_kind: str,
        evidence_records: Sequence[LogRecord],
        event: Mapping[str, Any] | None = None,
        warnings: Sequence[str] = (),
    ) -> dict[str, Any]:
        row = claim_account_role(post_id)
        record_publication_evidence(
            row,
            authority=authority,
            evidence_kind=evidence_kind,
            evidence_records=evidence_records,
        )
        _merge_account_graph_evidence(
            row,
            {
                "account_graph_kind": "root",
                "conversation_id": post_id,
                "lane": lane,
                "parent_observation_status": "confirmed_none",
                "parent_post_id": None,
                "quote_id": quote_id,
                "root_post_id": post_id,
            },
            authority=authority,
        )
        _merge_account_content_evidence(
            row,
            {
                "public_text": public_text,
                "text": text,
                "text_source": text_source,
                "visible_media_text": visible_media_text,
            },
            authority=authority,
        )
        if row.get("text") is None:
            _record_warning(row, "confirmed_account_root_text_unavailable")
            if not row.get("account_content_ambiguity_authority"):
                _raise_reconstruction_confidence(row, "medium")
        if event is not None:
            _set_explicit_creation_time(
                row,
                event,
                ("post_created_at",),
                record,
                subject="root",
            )
        for warning in warnings:
            if warning not in ACCOUNT_UNAVAILABLE_TEXT_WARNINGS or row.get("text") is None:
                _record_warning(row, warning)
        return row

    def publish_historical_context(
        *,
        reply_id: str,
        parent_id: str,
        record: LogRecord,
        text: str | None,
        quote_id: str | None,
        authority: str,
        evidence_kind: str,
        evidence_records: Sequence[LogRecord],
        event: Mapping[str, Any] | None = None,
        warnings: Sequence[str] = (),
    ) -> dict[str, Any]:
        root_is_authoritative = parent_id in authoritative_account_ids
        if root_is_authoritative:
            claim_account_role(parent_id)
        row = claim_account_role(reply_id)
        record_publication_evidence(
            row,
            authority=authority,
            evidence_kind=evidence_kind,
            evidence_records=evidence_records,
        )
        _merge_account_graph_evidence(
            row,
            {
                "account_graph_kind": "reply",
                "conversation_id": parent_id,
                "lane": "historical_context_reply",
                "parent_observation_status": "observed",
                "parent_post_id": parent_id,
                "quote_id": quote_id,
                "root_post_id": parent_id,
            },
            authority=authority,
        )
        _merge_account_content_evidence(
            row,
            {
                "public_text": text,
                "text": text,
                "text_source": "historical_context_reply" if text else "unavailable",
                "visible_media_text": None,
            },
            authority=authority,
        )
        # The publication contract proves the reply's graph identity even when
        # retained evidence for the parent root is unavailable.  It does not,
        # by itself, promote that absent parent to an account-authored post.
        if not root_is_authoritative:
            _record_warning(row, "authoritative_account_root_unavailable")
        if row.get("text") is None:
            _record_warning(row, "confirmed_historical_context_text_unavailable")
            if not row.get("account_content_ambiguity_authority"):
                _raise_reconstruction_confidence(row, "medium")
        if event is not None:
            _set_explicit_creation_time(
                row,
                event,
                ("reply_created_at",),
                record,
                subject="reply",
            )
        for warning in warnings:
            if warning not in ACCOUNT_UNAVAILABLE_TEXT_WARNINGS or row.get("text") is None:
                _record_warning(row, warning)
        return row

    def publish_account(
        target_id: str,
        reply_post_id: str,
        record: LogRecord,
        *,
        authority: str,
        evidence_kind: str,
        event: Mapping[str, Any] | None = None,
        contract: StructuredEventContract | None = None,
    ) -> dict[str, Any]:
        target = get_user(target_id)
        row = claim_account_role(reply_post_id)
        record_publication_evidence(
            row,
            authority=authority,
            evidence_kind=evidence_kind,
            evidence_records=(record,),
        )
        row["parent_post_id"] = target_id
        row["parent_observation_status"] = "observed"
        row["lane"] = target.get("lane") or "other conversational lane"
        for field in ("conversation_id", "root_post_id", "thread_id"):
            if target.get(field):
                row[field] = target[field]
        selected_attempt, binding_warnings = _bind_confirmed_send_attempt(
            target,
            record=record,
            reply_post_id=reply_post_id,
        )
        target_attempts = _bounded_attempts(target)
        final_text = (
            str(selected_attempt.get("text"))
            if selected_attempt is not None and selected_attempt.get("text")
            else (
                str(target.get("generated_reply_text") or "")
                if not target_attempts
                else ""
            )
        )
        _set_text(row, final_text, source="public_text", public_text=True)
        for warning in binding_warnings:
            _record_warning(row, warning)
        if event:
            for field in (contract.identity_fields if contract else ()):
                _set_identity(row, field, event.get(field))
            if contract:
                _set_explicit_creation_time(
                    row,
                    event,
                    contract.reply_creation_fields,
                    record,
                    subject="reply",
                )
            if event.get("route_source"):
                row["route_source"] = str(event["route_source"])
            if event.get("reply_requirement"):
                row["reply_requirement"] = str(event["reply_requirement"])
        if row.get("text") is None:
            _record_warning(row, "confirmed_account_reply_text_unavailable")
            row["reconstruction_confidence"] = "medium"
        else:
            row["reconstruction_confidence"] = "high"
        row["account_turn_asked_for_clarification"] = _looks_like_clarification_request(
            row.get("text")
        )
        return row

    for record in ordered_records:
        message = record.message
        media_context = parse_reply_media_context_observation(record)
        if media_context is not None:
            media_target_id, media_observation = media_context
            media_target = get_user(media_target_id)
            if media_target.get("author_role") != "account":
                media_target["lane"] = str(media_observation["lane"])
            _merge_bounded_reply_observations(
                media_target,
                field="reply_media_context_observations",
                other_count_field="reply_media_context_other_count",
                additions=(media_observation,),
                maximum=MAX_REPLY_MEDIA_CONTEXT_OBSERVATIONS,
            )
            _touch_post(media_target, record)

        match = CONSIDER_RE.search(message)
        if match:
            target_id = match.group(2)
            row = get_user(target_id)
            if row.get("author_role") == "account":
                _record_account_self_observation(row, record)
            else:
                row["lane"] = normalise_lane(match.group(1))
                _set_author(row, pseudonym_key, match.group(3))
                _set_text(
                    row,
                    decode_literal(match.group(4)),
                    source="mention_observation",
                    public_text=True,
                )
            _touch_post(row, record)
            if _confidence_rank(row.get("reconstruction_confidence")) < 1:
                row["reconstruction_confidence"] = "medium"

        match = QUOTE_RE.search(message)
        if match:
            target_id = match.group(1)
            row = get_user(target_id)
            if row.get("author_role") == "account":
                _record_account_self_observation(row, record)
            else:
                row["lane"] = "quote-tweet reply"
                _set_author(row, pseudonym_key, match.group(2))
                _set_text(
                    row,
                    decode_literal(match.group(4)),
                    source="mention_observation",
                    public_text=True,
                )
                row["quoted_post_id"] = match.group(3)
                row["parent_observation_status"] = "confirmed_none"
                row["reconstruction_confidence"] = "high"
            _touch_post(row, record)

        match = CONTEXT_RE.search(message)
        if match:
            target_id = match.group(1)
            row = get_user(target_id)
            parent = match.group(3)
            chain_items = int(match.group(2))
            if row.get("author_role") == "account":
                _record_account_self_observation(row, record)
            elif parent.casefold() in {"none", "null", "unavailable", ""}:
                if chain_items == 0:
                    row["parent_post_id"] = None
                    row["parent_observation_status"] = "confirmed_none"
                    row["reconstruction_confidence"] = "high"
                else:
                    row["parent_observation_status"] = "ambiguous"
                    row["reconstruction_confidence"] = "low"
                    _record_warning(row, "parent_chain_count_without_parent_identity")
            else:
                _set_identity(row, "parent_post_id", parent)
                row["parent_observation_status"] = "observed"
                row["reconstruction_confidence"] = "high"
            if row.get("author_role") != "account":
                row["parent_thread_entry_count"] = chain_items
            _touch_post(row, record)

        match = SINGLE_CALL_CONTEXT_RE.search(message)
        if match:
            target_id = match.group(1)
            row = get_user(target_id)
            turn_count = int(match.group(2))
            root_id = match.group(3)
            parent_id = match.group(4)
            if row.get("author_role") == "account":
                _record_account_self_observation(row, record)
            else:
                _set_identity(row, "root_post_id", root_id)
                if parent_id.casefold() in {"none", "null", "unavailable", ""}:
                    row["parent_post_id"] = None
                    row["parent_observation_status"] = "confirmed_none"
                else:
                    _set_identity(row, "parent_post_id", parent_id)
                    row["parent_observation_status"] = "observed"
                row["parent_thread_entry_count"] = max(0, turn_count - 1)
                row["reconstruction_confidence"] = "high"
            _touch_post(row, record)

        match = GENERATED_RE.search(message)
        if match:
            target_id = match.group(2)
            row = get_user(target_id)
            if row.get("author_role") == "account":
                _record_account_self_observation(row, record)
            else:
                row["generated_reply_text"] = decode_literal(match.group(3))
                row["generated_reply_text_observed_at"] = record.timestamp
                row["lane"] = normalise_lane(match.group(1))
            _touch_post(row, record)

        match = GENERATED_QUOTE_RE.search(message)
        if match:
            target_id = match.group(1)
            row = get_user(target_id)
            if row.get("author_role") == "account":
                _record_account_self_observation(row, record)
            else:
                row["generated_reply_text"] = decode_literal(match.group(2))
                row["generated_reply_text_observed_at"] = record.timestamp
                row["lane"] = "quote-tweet reply"
            _touch_post(row, record)

        match = CREATE_ATTEMPT_RE.search(message)
        if match:
            lane = match.group(1)
            transaction_id = match.group(2)
            target_id = match.group(3)
            active_attempt = None
            if (
                lane == "conversational_reply"
                and target_id.casefold() not in {"none", "null"}
            ):
                target = get_user(target_id)
                text_value = decode_literal(match.group(4))
                _touch_post(target, record)
                target["generated_reply_text"] = text_value
                target["generated_reply_text_observed_at"] = record.timestamp
                _upsert_send_attempt(
                    target,
                    transaction_id=transaction_id,
                    target_post_id=target_id,
                    lane=lane,
                    text=text_value,
                    record=record,
                )
                active_attempt = (target_id, transaction_id)

        match = CREATED_ID_RE.search(message)
        if match:
            if active_attempt is not None:
                target_id, transaction_id = active_attempt
                _mark_send_attempt(
                    get_user(target_id),
                    record=record,
                    status_value="remote_success_observed",
                    transaction_id=transaction_id,
                    reply_post_id=match.group(1),
                )
            active_attempt = None

        if FAILED_GENERATED_REPLY_RE.search(message) and active_attempt is not None:
            target_id, transaction_id = active_attempt
            _mark_send_attempt(
                get_user(target_id),
                record=record,
                status_value="failed",
                transaction_id=transaction_id,
            )
            active_attempt = None

        match = REMOVED_SENDING_RECEIPT_RE.search(message)
        if match:
            target_id = match.group(3)
            target = get_user(target_id)
            open_attempts = [
                attempt
                for attempt in _bounded_attempts(target)
                if attempt.get("last_observed_status")
                in {"started", "remote_success_observed"}
            ]
            if len(open_attempts) == 1:
                _mark_send_attempt(
                    target,
                    record=record,
                    status_value="retired",
                    transaction_id=str(open_attempts[0]["transaction_id"]),
                )
            elif open_attempts:
                _record_warning(target, "ambiguous_send_attempt_retirement")

        match = PROMOTED_RE.search(message)
        if match:
            target_id = match.group(2)
            target = get_user(target_id)
            if target.get("author_role") != "account":
                target["lane"] = normalise_lane(match.group(1))
            publish_account(
                target_id,
                match.group(3),
                record,
                authority="confirmed_receipt_promotion",
                evidence_kind="confirmed_receipt_promotion",
            )

        event = record.structured_event
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event") or event.get("kind") or "")
        if kind == "reply_visual_description":
            visual_target_id, visual_attempt = (
                parse_reply_visual_description_attempt(event, record)
            )
            if visual_target_id is None:
                statistics["registered_event_missing_target_count"] += 1
            elif visual_attempt is None:
                statistics["ambiguous_registered_event_count"] += 1
                _record_warning(
                    get_user(visual_target_id),
                    "malformed_reply_visual_description_event",
                )
            else:
                visual_target = get_user(visual_target_id)
                if visual_target.get("author_role") != "account":
                    visual_target["lane"] = str(visual_attempt["lane"])
                _merge_bounded_reply_observations(
                    visual_target,
                    field="reply_visual_description_attempts",
                    other_count_field="reply_visual_description_other_count",
                    additions=(visual_attempt,),
                    maximum=MAX_REPLY_VISUAL_DESCRIPTION_ATTEMPTS,
                )
                _touch_post(visual_target, record)
            continue
        if kind == "account_root_posted":
            if not _valid_account_root_event(event):
                statistics["ambiguous_registered_event_count"] += 1
                continue
            post_id = str(event["post_id"])
            public = str(event.get("public_text") or "").strip() or None
            quote = str(event.get("quote_text") or "").strip() or None
            image_summary = str(event.get("image_summary") or "").strip() or None
            if public:
                visible = public
                source = "public_text"
            elif quote:
                visible = quote
                source = "image_quote_text"
            elif image_summary:
                visible = image_summary
                source = "image_summary"
            else:
                visible = None
                source = "unavailable"
            declared_visible = str(event.get("visible_text") or "").strip() or None
            declared_source = str(event.get("visible_text_source") or "")
            row = publish_account_root(
                post_id=post_id,
                record=record,
                lane=normalise_account_lane(event.get("lane")),
                text=visible,
                text_source=source,
                public_text=public,
                visible_media_text=quote or image_summary,
                quote_id=str(event.get("quote_id") or "") or None,
                authority="structured_confirmation",
                evidence_kind=kind,
                evidence_records=(record,),
                event=event,
            )
            if declared_visible != visible or declared_source != source:
                _record_warning(row, "account_root_visible_text_contract_mismatch")
            continue
        if kind == "historical_context_reply_posted":
            if not _valid_historical_context_event(event):
                statistics["ambiguous_registered_event_count"] += 1
                continue
            publish_historical_context(
                reply_id=str(event["reply_post_id"]),
                parent_id=str(event["parent_post_id"]),
                record=record,
                text=str(event.get("reply_text") or "").strip() or None,
                quote_id=str(event.get("quote_id") or "") or None,
                authority="structured_confirmation",
                evidence_kind=kind,
                evidence_records=(record,),
                event=event,
            )
            continue
        contract = STRUCTURED_CONVERSATION_EVENT_FIELDS.get(kind)
        if contract is None:
            if kind in LEGACY_ACCOUNT_SEQUENCE_EVENT_KINDS:
                continue
            statistics["ignored_structured_event_count"] += 1
            histogram_kind = kind or "unavailable"
            kind_counts = statistics["ignored_structured_event_kinds_by_kind"]
            kind_counts[histogram_kind] = int(
                kind_counts.get(histogram_kind, 0)
            ) + 1
            target_like = any(
                event.get(field) is not None
                for field in (
                    "id",
                    "target_id",
                    "mention_id",
                    "hot_post_reply_id",
                    "quote_tweet_id",
                    "author_id",
                    "incoming_text",
                    "incoming_contribution",
                )
            )
            if target_like:
                statistics["ignored_target_like_event_count"] += 1
                target_counts = statistics[
                    "ignored_target_like_event_kinds_by_kind"
                ]
                target_counts[histogram_kind] = int(
                    target_counts.get(histogram_kind, 0)
                ) + 1
            continue
        target_id, target_ambiguous = _registered_value(
            event, contract.target_fields
        )
        if target_ambiguous:
            statistics["ambiguous_registered_event_count"] += 1
            continue
        if not target_id:
            statistics["registered_event_missing_target_count"] += 1
            continue
        target = get_user(target_id)
        _touch_post(target, record)
        account_self_observation = target.get("author_role") == "account"
        if account_self_observation:
            _record_account_self_observation(target, record)
        elif event.get("lane") or event.get("candidate_source") or event.get("source"):
            target["lane"] = normalise_lane(
                event.get("lane") or event.get("candidate_source") or event.get("source")
            )
        if not account_self_observation:
            raw_author, author_ambiguous = _registered_value(
                event, contract.author_fields
            )
            if author_ambiguous:
                _record_warning(target, "conflicting_registered_author_fields")
            elif raw_author:
                _set_author(target, pseudonym_key, raw_author)
        if not account_self_observation:
            for field in contract.identity_fields:
                _set_identity(target, field, event.get(field))
            parent_value, parent_ambiguous = _registered_value(
                event, contract.parent_fields
            )
            if parent_ambiguous:
                _record_warning(target, "conflicting_registered_parent_fields")
                target["parent_observation_status"] = "ambiguous"
                target["reconstruction_confidence"] = "low"
            elif parent_value:
                _set_identity(target, "parent_post_id", parent_value)
                target["parent_observation_status"] = "observed"
                target["reconstruction_confidence"] = "high"
            for text_field in contract.incoming_text_fields:
                if event.get(text_field):
                    _set_text(
                        target,
                        event[text_field],
                        source="mention_observation",
                        public_text=True,
                    )
                    break
        _set_explicit_creation_time(
            target,
            event,
            contract.target_creation_fields,
            record,
            subject="target",
        )
        if event.get("route_source") is not None:
            target["route_source"] = str(event["route_source"])
        if event.get("reply_requirement") is not None:
            target["reply_requirement"] = str(event["reply_requirement"])
        supplied_count = event.get("trusted_facts_supplied_count")
        if type(supplied_count) is int and supplied_count >= 0:
            target["trusted_fact_count"] = supplied_count
        supplied_ids = event.get("trusted_fact_ids_supplied")
        if isinstance(supplied_ids, list):
            target["trusted_fact_ids"] = sorted(
                {str(value) for value in supplied_ids if str(value)}
            )
        if kind in PIPELINE_EVENT_KINDS:
            summary = _pipeline_summary(kind, event, record)
            target["pipeline_stage_summaries"] = _merge_unique_objects(
                list(target.get("pipeline_stage_summaries") or []), [summary]
            )
            if kind.startswith("ai_reply_pipeline"):
                target["tested_pipeline_stage_summaries"] = _merge_unique_objects(
                    list(target.get("tested_pipeline_stage_summaries") or []), [summary]
                )

        reply_post_id = str(event.get("reply_post_id") or "").strip()
        if contract.confirms_publication and reply_post_id:
            publish_account(
                target_id,
                reply_post_id,
                record,
                authority="structured_confirmation",
                evidence_kind=kind,
                event=event,
                contract=contract,
            )

    for publication in sorted(
        legacy_publications.values(),
        key=lambda item: (item.evidence_records[-1].timestamp, item.post_id),
    ):
        record = publication.evidence_records[-1]
        if publication.parent_post_id is None:
            publish_account_root(
                post_id=publication.post_id,
                record=record,
                lane=publication.lane,
                text=publication.text,
                text_source=publication.text_source,
                public_text=publication.public_text,
                visible_media_text=publication.visible_media_text,
                quote_id=publication.quote_id,
                authority="legacy_confirmed_sequence",
                evidence_kind=publication.evidence_kind,
                evidence_records=publication.evidence_records,
                warnings=publication.warnings,
            )
        else:
            publish_historical_context(
                reply_id=publication.post_id,
                parent_id=publication.parent_post_id,
                record=record,
                text=publication.text,
                quote_id=publication.quote_id,
                authority="legacy_confirmed_sequence",
                evidence_kind=publication.evidence_kind,
                evidence_records=publication.evidence_records,
                warnings=publication.warnings,
            )

    for post in posts.values():
        _ensure_account_evidence_defaults(post)
        post["source_provenance"] = _merge_unique_objects(
            [], post.get("source_provenance") or []
        )
        post["pipeline_stage_summaries"] = _merge_unique_objects(
            [], post.get("pipeline_stage_summaries") or []
        )
        post["tested_pipeline_stage_summaries"] = _merge_unique_objects(
            [], post.get("tested_pipeline_stage_summaries") or []
        )
        _merge_bounded_reply_observations(
            post,
            field="reply_media_context_observations",
            other_count_field="reply_media_context_other_count",
            additions=(),
            maximum=MAX_REPLY_MEDIA_CONTEXT_OBSERVATIONS,
        )
        _merge_bounded_reply_observations(
            post,
            field="reply_visual_description_attempts",
            other_count_field="reply_visual_description_other_count",
            additions=(),
            maximum=MAX_REPLY_VISUAL_DESCRIPTION_ATTEMPTS,
        )
        post["reply_visual_context_summary"] = (
            _derive_reply_visual_context_summary(post)
        )
        post["warnings"] = sorted(set(post.get("warnings") or []))
        post["trusted_fact_ids"] = sorted(set(post.get("trusted_fact_ids") or []))
        post["creation_time_provenance"] = _merge_unique_objects(
            [], post.get("creation_time_provenance") or []
        )
        post["creation_time_conflict"] = bool(
            post.get("creation_time_conflict", False)
        )
        post["creation_time_conflicts"] = _merge_unique_objects(
            [], post.get("creation_time_conflicts") or []
        )
        post["publication_evidence"] = _merge_unique_objects(
            [], post.get("publication_evidence") or []
        )
        _merge_bounded_account_conflicts(post, "account_graph_conflicts", ())
        _merge_bounded_account_conflicts(post, "account_content_conflicts", ())
        post["account_graph_ambiguous_fields"] = sorted(
            set(post.get("account_graph_ambiguous_fields") or [])
        )
        post["graph_field_evidence_authorities"] = {
            str(field): str(authority)
            for field, authority in sorted(
                dict(post.get("graph_field_evidence_authorities") or {}).items()
            )
        }
        if post.get("text") is not None:
            _remove_warnings(post, ACCOUNT_UNAVAILABLE_TEXT_WARNINGS)
        if post.get("account_graph_ambiguity_authority"):
            post["reconstruction_confidence"] = "low"
        elif post.get("account_content_ambiguity_authority"):
            post["reconstruction_confidence"] = "medium"
        post["send_attempts"] = _bounded_attempts(post)
        post["self_observation_count"] = int(
            post.get("self_observation_count") or 0
        )
        if post.get("author_role") == "account":
            post["account_turn_asked_for_clarification"] = _looks_like_clarification_request(
                post.get("text")
            )
        if not post.get("canonical_event_id"):
            post["canonical_event_id"] = stable_id("post", post["post_id"])
        post["schema_version"] = SCHEMA_VERSION
        post["derivation_parser_version"] = PARSER_VERSION
    return sorted(
        posts.values(),
        key=lambda row: (
            str(
                row.get("created_at")
                or row.get("first_observed_at")
                or "9999"
            ),
            str(row.get("post_id") or ""),
        ),
    )


def ignored_structured_event_histogram(
    statistics: Mapping[str, Any],
    *,
    maximum_kinds: int = MAX_IGNORED_STRUCTURED_EVENT_KINDS,
) -> tuple[list[dict[str, Any]], int, int]:
    """Return a bounded count-first deterministic ignored-event histogram."""
    if maximum_kinds < 0:
        raise ValueError("maximum_kinds must be non-negative")
    raw_counts = statistics.get("ignored_structured_event_kinds_by_kind")
    raw_target_counts = statistics.get(
        "ignored_target_like_event_kinds_by_kind"
    )
    counts = {
        str(kind): int(count)
        for kind, count in (raw_counts.items() if isinstance(raw_counts, dict) else [])
        if type(count) is int and count > 0
    }
    target_counts = {
        str(kind): int(count)
        for kind, count in (
            raw_target_counts.items()
            if isinstance(raw_target_counts, dict)
            else []
        )
        if type(count) is int and count > 0
    }
    ordered = sorted(counts, key=lambda kind: (-counts[kind], kind))
    retained = ordered[:maximum_kinds]
    omitted = ordered[maximum_kinds:]
    rows = [
        {
            "count": counts[kind],
            "event_kind": kind,
            "target_like_count": target_counts.get(kind, 0),
        }
        for kind in retained
    ]
    return (
        rows,
        sum(counts[kind] for kind in omitted),
        sum(target_counts.get(kind, 0) for kind in omitted),
    )


@dataclass(frozen=True)
class SubstantiveResult:
    """Describe whether text is substantive and the deterministic reason."""

    substantive: bool
    reason: str


ROUTINE_SINGLE_WORDS = frozenset(
    {"cheers", "hello", "hey", "hi", "morning", "thanks", "thankyou"}
)


def substantive_result(text: Any) -> SubstantiveResult:
    """Apply conservative deterministic non-substantive exclusions."""
    original = str(text or "")
    stripped = original.strip()
    if not stripped:
        return SubstantiveResult(False, "whitespace_only")
    if re.fullmatch(r"@[A-Za-z0-9_]+", stripped):
        return SubstantiveResult(False, "bare_handle")
    if re.fullmatch(r"https?://\S+", stripped, re.I):
        return SubstantiveResult(False, "bare_url")
    if len(stripped) == 1 and unicodedata.category(stripped).startswith("P"):
        return SubstantiveResult(False, "single_punctuation_mark")
    word_tokens = re.findall(r"[\w']+", stripped, flags=re.UNICODE)
    if not word_tokens and all(
        unicodedata.category(character)[0] in {"P", "S", "Z"}
        for character in stripped
    ):
        return SubstantiveResult(False, "isolated_symbol_or_emoji")
    lowered_words = "".join(word_tokens).casefold()
    if len(word_tokens) == 1 and lowered_words in ROUTINE_SINGLE_WORDS:
        return SubstantiveResult(False, "routine_one_word_greeting_or_thanks")
    return SubstantiveResult(True, "meaningful_text_retained")


def correction_cues(text: Any) -> list[str]:
    """Return deterministic correction-cue labels found in text."""
    candidate = str(text or "")
    return [
        label
        for label, pattern in CORRECTION_CUE_PATTERNS
        if pattern.search(candidate)
    ]


class _DisjointSet:
    def __init__(self, posts: Mapping[str, Mapping[str, Any]]) -> None:
        self.parent = {post_id: post_id for post_id in posts}
        self.rank = {post_id: 0 for post_id in posts}
        self.conversations = {
            post_id: ({str(row["conversation_id"])} if row.get("conversation_id") else set())
            for post_id, row in posts.items()
        }
        self.roots = {
            post_id: ({str(row["root_post_id"])} if row.get("root_post_id") else set())
            for post_id, row in posts.items()
        }

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return True
        left_conversations = self.conversations[left_root]
        right_conversations = self.conversations[right_root]
        left_roots = self.roots[left_root]
        right_roots = self.roots[right_root]
        if left_conversations and right_conversations and left_conversations.isdisjoint(
            right_conversations
        ):
            return False
        if left_roots and right_roots and left_roots.isdisjoint(right_roots):
            return False
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1
        self.conversations[left_root] |= self.conversations.pop(right_root)
        self.roots[left_root] |= self.roots.pop(right_root)
        return True


def _component_min_confidence(turns: Sequence[Mapping[str, Any]]) -> str:
    return min(
        (str(turn.get("reconstruction_confidence") or "low") for turn in turns),
        key=_confidence_rank,
        default="low",
    )


def _conversation_key(
    turns: Sequence[Mapping[str, Any]],
    conversation_ids: set[str],
    root_ids: set[str],
    root_post_id: str | None,
) -> str:
    if len(conversation_ids) == 1:
        basis = f"conversation-id:{next(iter(conversation_ids))}"
    elif len(root_ids) == 1:
        basis = f"root-id:{next(iter(root_ids))}"
    elif root_post_id:
        basis = f"derived-root:{root_post_id}"
    else:
        first = turns[0]
        parent = str(first.get("parent_post_id") or "")
        basis = f"parent-chain:{parent}" if parent else f"orphan:{first['post_id']}"
    return stable_id("conversation", basis)


def _turn_order_key(turn: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(turn.get("created_at") or turn.get("first_observed_at") or "9999"),
        str(turn.get("post_id") or ""),
        str(turn.get("canonical_event_id") or ""),
    )


@dataclass(frozen=True)
class ConversationStart:
    """Describe the resolved conversation start time and its provenance."""

    value: datetime | None
    source: str
    root_post_id: str | None
    warning: str | None = None


def _resolve_conversation_start(
    turns: Sequence[Mapping[str, Any]],
    root_ids: set[str],
    conversation_ids: set[str],
) -> ConversationStart:
    if not turns:
        return ConversationStart(None, "unavailable", None)
    by_id = {str(turn.get("post_id")): turn for turn in turns}
    earliest_observation = min(
        (
            value
            for turn in turns
            if (value := parse_optional_timestamp(turn.get("first_observed_at")))
            is not None
        ),
        default=None,
    )
    root_post_id = next(iter(root_ids)) if len(root_ids) == 1 else None
    conversation_id = (
        next(iter(conversation_ids)) if len(conversation_ids) == 1 else None
    )

    if (
        any(
            "authoritative_account_root_unavailable"
            in set(turn.get("warnings") or [])
            for turn in turns
        )
        and root_post_id
        and root_post_id not in by_id
    ):
        return ConversationStart(
            None,
            "unavailable",
            root_post_id,
            "authoritative_account_root_unavailable",
        )

    for identity in (root_post_id, conversation_id):
        if identity and identity in by_id:
            created = parse_optional_timestamp(by_id[identity].get("created_at"))
            if created is not None:
                return ConversationStart(created, "root_post", identity)

    if root_post_id:
        decoded, warning = decode_x_snowflake_time(
            root_post_id,
            first_observed_at=format_utc(earliest_observation),
        )
        if decoded is not None:
            return ConversationStart(
                decoded, "root_post_id_snowflake", root_post_id
            )
        root_warning = f"root_post_id_{warning}" if warning else None
    else:
        root_warning = None

    if conversation_id:
        decoded, warning = decode_x_snowflake_time(
            conversation_id,
            first_observed_at=format_utc(earliest_observation),
        )
        if decoded is not None:
            return ConversationStart(
                decoded,
                "conversation_id_snowflake",
                root_post_id or conversation_id,
            )
        conversation_warning = (
            f"conversation_id_{warning}" if warning else None
        )
    else:
        conversation_warning = None

    first = min(turns, key=_turn_order_key)
    first_created = parse_optional_timestamp(first.get("created_at"))
    if (
        first_created is not None
        and first.get("parent_observation_status") == "confirmed_none"
        and not first.get("parent_post_id")
    ):
        return ConversationStart(
            first_created,
            "confirmed_root_turn",
            root_post_id or str(first.get("post_id") or "") or None,
        )
    return ConversationStart(
        None,
        "unavailable",
        root_post_id,
        root_warning or conversation_warning,
    )


def _resolve_last_activity(
    turns: Sequence[Mapping[str, Any]],
) -> tuple[datetime | None, str]:
    values: list[tuple[datetime, int, str]] = []
    for turn in turns:
        created = parse_optional_timestamp(turn.get("created_at"))
        if created is not None:
            values.append((created, 1, "post_created_at"))
            continue
        observed = parse_optional_timestamp(
            turn.get("last_observed_at") or turn.get("first_observed_at")
        )
        if observed is not None:
            values.append((observed, 0, "observation_fallback"))
    if not values:
        return None, "observation_fallback"
    value, _priority, source = max(values, key=lambda item: (item[0], item[1]))
    return value, source


def build_conversations(
    posts: Sequence[Mapping[str, Any]],
    *,
    boundary: datetime,
    cutoff: datetime,
    quiescence_hours: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group canonical posts without ever using author identity as a join key."""
    by_id = {str(row["post_id"]): copy.deepcopy(dict(row)) for row in posts}
    dsu = _DisjointSet(by_id)
    blocked: dict[str, set[str]] = defaultdict(set)

    def guarded_union(left: str, right: str, warning: str) -> None:
        if left == right or left not in by_id or right not in by_id:
            return
        if not dsu.union(left, right):
            blocked[left].add(warning)
            blocked[right].add(warning)

    for field, warning in (
        ("conversation_id", "conflicting_conversation_identity"),
        ("root_post_id", "conflicting_root_identity"),
    ):
        groups: dict[str, list[str]] = defaultdict(list)
        for post_id, row in by_id.items():
            if row.get(field):
                groups[str(row[field])].append(post_id)
        for members in groups.values():
            first = min(members)
            for member in sorted(members):
                guarded_union(first, member, warning)

    for post_id, row in sorted(by_id.items()):
        parent = str(row.get("parent_post_id") or "")
        if parent in by_id:
            guarded_union(parent, post_id, "ambiguous_parentage")

    thread_groups: dict[str, list[str]] = defaultdict(list)
    for post_id, row in by_id.items():
        if row.get("thread_id"):
            thread_groups[str(row["thread_id"])].append(post_id)
    for members in thread_groups.values():
        first = min(members)
        for member in sorted(members):
            guarded_union(first, member, "conflicting_thread_identity")

    external_parent_groups: dict[str, list[str]] = defaultdict(list)
    for post_id, row in by_id.items():
        parent = str(row.get("parent_post_id") or "")
        if parent and parent not in by_id:
            external_parent_groups[parent].append(post_id)
    for members in external_parent_groups.values():
        first = min(members)
        for member in sorted(members):
            guarded_union(first, member, "ambiguous_parentage")

    components: dict[str, list[str]] = defaultdict(list)
    for post_id in by_id:
        components[dsu.find(post_id)].append(post_id)

    conversations: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    boundary_text = str(format_utc(boundary))
    for component_ids_list in components.values():
        component_ids = set(component_ids_list)
        raw_turns = [by_id[post_id] for post_id in component_ids]
        raw_turns.sort(key=_turn_order_key)
        if not raw_turns:
            continue
        latest_observation = max(
            (
                str(turn.get("last_observed_at"))
                for turn in raw_turns
                if turn.get("last_observed_at")
            ),
            default="",
        )
        if not latest_observation or latest_observation < boundary_text:
            continue
        conversation_ids = {
            str(turn["conversation_id"])
            for turn in raw_turns
            if turn.get("conversation_id")
        }
        root_ids = {
            str(turn["root_post_id"])
            for turn in raw_turns
            if turn.get("root_post_id")
        }
        start = _resolve_conversation_start(
            raw_turns, root_ids, conversation_ids
        )
        start_value = start.value
        last_value, last_activity_source = _resolve_last_activity(raw_turns)
        if start_value is not None and start_value < boundary:
            prospective_status = "pre_boundary"
        elif start_value is not None:
            prospective_status = "eligible"
        else:
            prospective_status = "start_unknown"
        root_post_id = start.root_post_id
        conversation_id = next(iter(conversation_ids)) if len(conversation_ids) == 1 else None
        conversation_key = _conversation_key(
            raw_turns, conversation_ids, root_ids, root_post_id
        )

        warnings: set[str] = set()
        turns: list[dict[str, Any]] = []
        for index, raw_turn in enumerate(raw_turns, start=1):
            turn = copy.deepcopy(raw_turn)
            turn["chronology_index"] = index
            result = substantive_result(turn.get("text"))
            turn["substantive"] = result.substantive
            turn["substantive_reason"] = result.reason
            turn["correction_cues"] = correction_cues(turn.get("text"))
            turn["turn_id"] = stable_id("turn", turn["post_id"])
            turns.append(turn)
            warnings.update(str(value) for value in turn.get("warnings") or [])
            warnings.update(blocked.get(str(turn["post_id"]), set()))
            parent = str(turn.get("parent_post_id") or "")
            if parent and parent not in component_ids:
                warnings.add("missing_parent_post")
        if prospective_status == "start_unknown":
            warnings.add("conversation_start_time_unavailable")
        if start.warning:
            warnings.add(start.warning)
        if len(conversation_ids) > 1:
            warnings.add("ambiguous_conversation_identity")
        if len(root_ids) > 1:
            warnings.add("ambiguous_root_identity")

        user_turns = [turn for turn in turns if turn.get("author_role") == "user"]
        account_turns = [turn for turn in turns if turn.get("author_role") == "account"]
        substantive_turns = [turn for turn in turns if turn.get("substantive")]
        incomplete = (
            any(turn.get("text") is None for turn in turns)
            or bool(warnings & {
                "ambiguous_conversation_identity",
                "ambiguous_parentage",
                "ambiguous_root_identity",
                "conversation_start_time_unavailable",
                "missing_parent_post",
            })
        )
        confidence = _component_min_confidence(turns)
        if prospective_status == "start_unknown":
            confidence = "low"
        elif incomplete and confidence == "high":
            confidence = "medium"
        author_key = next(
            (str(turn["author_key"]) for turn in user_turns if turn.get("author_key")),
            next(
                (
                    str(turn["author_key"])
                    for turn in turns
                    if turn.get("author_key")
                ),
                stable_id("unknown-author", conversation_key),
            ),
        )
        last_value = last_value or cutoff
        activity_status = (
            "quiescent"
            if cutoff - last_value >= timedelta(hours=quiescence_hours)
            else "open"
        )
        lane_sequence = [
            str(turn.get("lane") or "other conversational lane") for turn in turns
        ]
        provenance = _merge_unique_objects(
            [],
            (
                item
                for turn in turns
                for item in turn.get("source_provenance") or []
            ),
        )
        conversation = {
            "account_turn_count": len(account_turns),
            "activity_status": activity_status,
            "author_key": author_key,
            "completeness": "partial" if incomplete else "complete",
            "conversation_id": conversation_id,
            "conversation_key": conversation_key,
            "lane_sequence": lane_sequence,
            "last_activity_time": format_utc(last_value),
            "last_activity_time_source": last_activity_source,
            "prospective_status": prospective_status,
            "reconstruction_confidence": confidence,
            "root_post_id": root_post_id,
            "schema_version": SCHEMA_VERSION,
            "source_provenance": provenance,
            "start_time": format_utc(start_value),
            "start_time_source": start.source,
            "substantive_turn_count": len(substantive_turns),
            "turns": turns,
            "user_turn_count": len(user_turns),
            "warnings": sorted(warnings),
        }
        conversations.append(conversation)
        candidates.extend(build_review_candidates(conversation))

    conversations.sort(
        key=lambda row: (str(row.get("start_time") or ""), row["conversation_key"])
    )
    candidates.sort(
        key=lambda row: (
            str(row.get("start_time") or ""),
            row["conversation_key"],
            row["principal_author_key"],
            row["segment_start_post_id"],
            row["branch_tip_post_id"],
        )
    )
    return conversations, candidates


def _flatten_pipeline_summaries(turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _merge_unique_objects(
        [],
        (
            summary
            for turn in turns
            for summary in turn.get("pipeline_stage_summaries") or []
        ),
    )


def _reply_visual_context_summaries(
    turns: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return safe summaries for image-bearing user turns on one path."""
    rows: list[dict[str, Any]] = []
    for turn in sorted(turns, key=_turn_order_key):
        if turn.get("author_role") != "user":
            continue
        summary = turn.get("reply_visual_context_summary")
        if not isinstance(summary, dict):
            continue
        native_photo_count = summary.get("native_photo_count_max")
        if type(native_photo_count) is not int or native_photo_count <= 0:
            continue
        rows.append(
            {
                "post_id": str(turn.get("post_id") or ""),
                **copy.deepcopy(summary),
            }
        )
    return rows


def _parent_path_to_tip(
    by_id: Mapping[str, Mapping[str, Any]], tip_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    reverse_path: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    current = tip_id
    while current in by_id:
        if current in seen:
            warnings.append("ambiguous_parentage")
            break
        seen.add(current)
        turn = copy.deepcopy(dict(by_id[current]))
        reverse_path.append(turn)
        parent = str(turn.get("parent_post_id") or "")
        if not parent:
            break
        if parent not in by_id:
            warnings.append("missing_parent_post")
            break
        current = parent
    reverse_path.reverse()
    return reverse_path, sorted(set(warnings))


def _bounded_sibling_context_refs(
    turns: Sequence[Mapping[str, Any]],
    path: Sequence[Mapping[str, Any]],
    *,
    excluded_post_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    path_ids = {str(turn.get("post_id") or "") for turn in path}
    excluded_ids = {str(value) for value in excluded_post_ids}
    relevant = [
        turn
        for turn in turns
        if str(turn.get("post_id") or "") not in path_ids
        and str(turn.get("post_id") or "") not in excluded_ids
        and str(turn.get("parent_post_id") or "") in path_ids
    ]
    relevant.sort(key=_turn_order_key)
    result: list[dict[str, Any]] = []
    for turn in relevant[:MAX_SIBLING_CONTEXT_REFS]:
        text = str(turn.get("text") or "")
        excerpt = text[:MAX_SIBLING_CONTEXT_TEXT_CHARS] if text else None
        result.append(
            {
                "author_key": turn.get("author_key"),
                "author_role": turn.get("author_role"),
                "created_at": turn.get("created_at"),
                "lane": turn.get("lane"),
                "parent_post_id": turn.get("parent_post_id"),
                "post_id": turn.get("post_id"),
                "text_excerpt": excerpt,
                "text_source": turn.get("text_source"),
            }
        )
    return result


def _handoff_context_ref(
    turn: Mapping[str, Any], *, position: str
) -> dict[str, Any]:
    text = str(turn.get("text") or "")
    return {
        "author_key": turn.get("author_key"),
        "author_role": turn.get("author_role"),
        "created_at": turn.get("created_at"),
        "lane": turn.get("lane"),
        "parent_post_id": turn.get("parent_post_id"),
        "position": position,
        "post_id": turn.get("post_id"),
        "text_excerpt": text[:MAX_SIBLING_CONTEXT_TEXT_CHARS] if text else None,
        "text_source": turn.get("text_source"),
    }


def _focused_author_segments(
    source_path: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Split a maximal parent path at each external-author hand-off."""
    user_turns = [
        (index, str(turn.get("author_key") or ""))
        for index, turn in enumerate(source_path)
        if turn.get("author_role") == "user" and turn.get("author_key")
    ]
    if not user_turns:
        return []
    grouped: list[list[tuple[int, str]]] = []
    for indexed_turn in user_turns:
        if not grouped or grouped[-1][-1][1] != indexed_turn[1]:
            grouped.append([indexed_turn])
        else:
            grouped[-1].append(indexed_turn)

    segments: list[dict[str, Any]] = []
    for group in grouped:
        first_user_index = group[0][0]
        last_user_index = group[-1][0]
        start_index = first_user_index
        if (
            first_user_index > 0
            and source_path[first_user_index - 1].get("author_role") == "account"
        ):
            start_index -= 1
        end_index = last_user_index
        if (
            last_user_index + 1 < len(source_path)
            and source_path[last_user_index + 1].get("author_role") == "account"
        ):
            end_index += 1
        focused_path = [
            copy.deepcopy(dict(turn))
            for turn in source_path[start_index : end_index + 1]
        ]
        context_refs: list[dict[str, Any]] = []
        if start_index > 0:
            context_refs.append(
                _handoff_context_ref(
                    source_path[start_index - 1], position="before_segment"
                )
            )
        if end_index + 1 < len(source_path):
            context_refs.append(
                _handoff_context_ref(
                    source_path[end_index + 1], position="after_segment"
                )
            )
        segments.append(
            {
                "handoff_context_refs": context_refs[:MAX_HANDOFF_CONTEXT_REFS],
                "path_turns": focused_path,
                "principal_author_key": group[0][1],
                "segment_start_post_id": str(focused_path[0]["post_id"]),
                "segment_tip_post_id": str(focused_path[-1]["post_id"]),
            }
        )
    return segments


def _same_author_continuation_depth(
    path: Sequence[Mapping[str, Any]], principal_author_key: str
) -> int:
    substantive_indices = [
        index
        for index, turn in enumerate(path)
        if turn.get("author_role") == "user"
        and turn.get("author_key") == principal_author_key
        and turn.get("substantive") is True
    ]
    depth = 0
    for previous, current in zip(substantive_indices, substantive_indices[1:]):
        if any(
            turn.get("author_role") == "account"
            for turn in path[previous + 1 : current]
        ):
            depth += 1
    return depth


def build_review_candidates(
    conversation: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Build focused contiguous author segments from maximal parent paths."""
    if conversation.get("prospective_status") != "eligible":
        return []
    turns = [
        copy.deepcopy(dict(turn))
        for turn in conversation.get("turns") or []
        if isinstance(turn, dict) and turn.get("post_id")
    ]
    by_id = {str(turn["post_id"]): turn for turn in turns}
    children: dict[str, set[str]] = defaultdict(set)
    for turn in turns:
        parent = str(turn.get("parent_post_id") or "")
        if parent in by_id:
            children[parent].add(str(turn["post_id"]))
    tips = sorted(post_id for post_id in by_id if not children.get(post_id))
    if not tips:
        tips = sorted(by_id)
    unique_paths: dict[tuple[str, ...], tuple[list[dict[str, Any]], list[str]]] = {}
    for tip_id in tips:
        path, path_warnings = _parent_path_to_tip(by_id, tip_id)
        identity = tuple(str(turn["post_id"]) for turn in path)
        unique_paths.setdefault(identity, (path, path_warnings))

    root_post_id = str(conversation.get("root_post_id") or "") or None
    segment_entries: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for _identity, (source_path, path_warnings) in sorted(unique_paths.items()):
        if not source_path:
            continue
        if root_post_id and str(source_path[0].get("post_id") or "") != root_post_id:
            path_warnings = sorted(
                set([*path_warnings, "root_not_reached_by_parent_path"])
            )
        source_path_ids = {
            str(turn.get("post_id") or "") for turn in source_path
        }
        source_branch_tip = str(source_path[-1]["post_id"])
        for segment in _focused_author_segments(source_path):
            path = list(segment["path_turns"])
            principal = str(segment["principal_author_key"])
            segment_identity = tuple(str(turn["post_id"]) for turn in path)
            key = (principal, segment_identity)
            sibling_refs = _bounded_sibling_context_refs(
                turns,
                path,
                excluded_post_ids=source_path_ids,
            )
            entry = segment_entries.setdefault(
                key,
                {
                    "chain_warnings": set(),
                    "handoff_context_refs": [],
                    "path_turns": path,
                    "principal_author_key": principal,
                    "segment_start_post_id": segment["segment_start_post_id"],
                    "segment_tip_post_id": segment["segment_tip_post_id"],
                    "sibling_context_refs": [],
                    "source_branch_tip_post_ids": set(),
                },
            )
            entry["chain_warnings"].update(path_warnings)
            entry["handoff_context_refs"] = _merge_unique_objects(
                list(entry["handoff_context_refs"]),
                segment["handoff_context_refs"],
            )[:MAX_HANDOFF_CONTEXT_REFS]
            entry["sibling_context_refs"] = _merge_unique_objects(
                list(entry["sibling_context_refs"]), sibling_refs
            )[:MAX_SIBLING_CONTEXT_REFS]
            entry["source_branch_tip_post_ids"].add(source_branch_tip)

    results: list[dict[str, Any]] = []
    for _key, entry in sorted(segment_entries.items()):
        path = list(entry["path_turns"])
        principal = str(entry["principal_author_key"])
        chain_warnings = sorted(entry["chain_warnings"])
        principal_turns = [
            turn
            for turn in path
            if turn.get("author_role") == "user"
            and turn.get("author_key") == principal
        ]
        if not principal_turns:
            continue
        account_turns = [
            turn for turn in path if turn.get("author_role") == "account"
        ]
        substantive_turns = [turn for turn in path if turn.get("substantive")]
        continuation_depth = _same_author_continuation_depth(path, principal)
        cues = sorted(
            {
                cue
                for turn in principal_turns
                for cue in turn.get("correction_cues") or []
            }
        )
        account_by_id = {
            str(turn["post_id"]): turn for turn in account_turns
        }
        post_clarification = any(
            account_by_id[parent_id].get("account_turn_asked_for_clarification")
            for turn in principal_turns
            if (parent_id := str(turn.get("parent_post_id") or ""))
            in account_by_id
        )
        sibling_refs = list(entry["sibling_context_refs"])
        handoff_refs = list(entry["handoff_context_refs"])
        warnings = {
            str(warning)
            for turn in path
            for warning in turn.get("warnings") or []
        }
        warnings.update(chain_warnings)
        if any(
            "ambiguous" in str(warning)
            for warning in conversation.get("warnings") or []
        ):
            warnings.add("ambiguous_parentage")
        partial = bool(
            any(turn.get("text") is None for turn in path)
            or warnings
            & {
                "ambiguous_parentage",
                "missing_parent_post",
                "root_not_reached_by_parent_path",
            }
        )
        reasons: set[str] = set()
        if continuation_depth:
            reasons.add("same_author_path_continuation")
        if len(account_turns) >= 2:
            reasons.add("multiple_account_replies_on_path")
        if len(substantive_turns) >= 3:
            reasons.add("third_or_later_substantive_path_turn")
        if cues:
            reasons.add("explicit_correction_cue")
        if post_clarification:
            reasons.add("post_clarification_continuation")
        if handoff_refs:
            reasons.add("external_author_handoff_context")
        if sibling_refs:
            reasons.add("sibling_branch_context")
        if partial:
            reasons.add("partial_path_reconstruction")
        if "ambiguous_parentage" in warnings:
            reasons.add("ambiguous_parentage")
        if not reasons:
            continue
        ordered_reasons = [
            reason for reason in REVIEW_REASON_ORDER if reason in reasons
        ]
        path_last, _path_last_source = _resolve_last_activity(path)
        confidence = _component_min_confidence(path)
        if partial and confidence == "high":
            confidence = "medium"
        segment_start_post_id = str(entry["segment_start_post_id"])
        branch_tip_post_id = str(entry["segment_tip_post_id"])
        source_branch_tip_post_ids = sorted(entry["source_branch_tip_post_ids"])
        source_branch_tip_post_id = source_branch_tip_post_ids[0]
        conversation_key = str(conversation["conversation_key"])
        branch_key = stable_id(
            "branch",
            conversation_key,
            principal,
            segment_start_post_id,
            branch_tip_post_id,
        )
        summaries = _flatten_pipeline_summaries(path)
        visual_summaries = _reply_visual_context_summaries(path)
        results.append(
            {
                "account_turn_count_on_path": len(account_turns),
                "activity_status": conversation.get("activity_status"),
                "branch_key": branch_key,
                "branch_tip_post_id": branch_tip_post_id,
                "candidate_key": stable_id("candidate", branch_key),
                "conversation_key": conversation_key,
                "correction_cues": cues,
                "handoff_context_refs": handoff_refs,
                "last_activity_time": format_utc(path_last),
                "path_turns": path,
                "pipeline_stage_summaries": summaries,
                "principal_author_key": principal,
                "prospective_status": "eligible",
                "reconstruction_confidence": confidence,
                "reply_visual_context_summaries": visual_summaries,
                "review_reason_codes": ordered_reasons,
                "root_post_id": root_post_id,
                "same_author_continuation_depth": continuation_depth,
                "same_author_user_turn_count": len(principal_turns),
                "schema_version": SCHEMA_VERSION,
                "segment_start_post_id": segment_start_post_id,
                "sibling_context_refs": sibling_refs,
                "source_branch_tip_post_id": source_branch_tip_post_id,
                "source_branch_tip_post_ids": source_branch_tip_post_ids,
                "start_time": path[0].get("created_at")
                or conversation.get("start_time"),
                "substantive_turn_count": len(substantive_turns),
                "substantive_turn_count_on_path": len(substantive_turns),
                "warnings": sorted(warnings),
            }
        )
    results.sort(
        key=lambda row: (
            str(row.get("start_time") or ""),
            str(row.get("conversation_key") or ""),
            str(row.get("principal_author_key") or ""),
            str(row.get("segment_start_post_id") or ""),
            str(row.get("branch_tip_post_id") or ""),
        )
    )
    return results


def build_review_candidate(
    conversation: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Compatibility wrapper returning the first deterministic branch candidate."""
    candidates = build_review_candidates(conversation)
    return candidates[0] if candidates else None


def _strict_read_json(path: Path) -> Any:
    try:
        descriptor = _open_regular_nofollow(path, os.O_RDONLY)
    except OSError as exc:
        raise ExtractorError(f"cannot open JSON file {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExtractorError(f"JSON path is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, HASH_BLOCK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        return strict_json_loads(b"".join(chunks))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ExtractorError(f"invalid strict JSON in {path}: {exc}") from exc


def read_extractor_state(root: Path, *, missing_ok: bool = True) -> dict[str, Any] | None:
    """Read and validate extractor state, optionally allowing its absence."""
    path = root / "state" / "extractor-state.json"
    if not path.exists() and not path.is_symlink():
        if missing_ok:
            return None
        raise ExtractorError(f"extractor state does not exist: {path}")
    value = _strict_read_json(path)
    if not isinstance(value, dict):
        raise ExtractorError("extractor state must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ExtractorError(
            f"unsupported extractor state schema: {value.get('schema_version')!r}"
        )
    if value.get("extractor_version") != EXTRACTOR_VERSION:
        raise ExtractorError(
            "extractor state version mismatch: stored "
            f"{value.get('extractor_version')!r}, running {EXTRACTOR_VERSION!r}"
        )
    if value.get("parser_version") != PARSER_VERSION:
        raise ExtractorError(
            "extractor parser version mismatch: stored "
            f"{value.get('parser_version')!r}, running {PARSER_VERSION!r}"
        )
    boundary = value.get("prospective_boundary")
    if not isinstance(boundary, str):
        raise ExtractorError("extractor state lacks a prospective boundary")
    parse_aware_timestamp(boundary, option="stored prospective boundary")
    source_cache = value.get("source_file_cache")
    if not isinstance(source_cache, dict):
        raise ExtractorError("extractor state source_file_cache must be an object")
    return value


def _atomic_write_state(root: Path, state_value: Mapping[str, Any]) -> None:
    state_dir = root / "state"
    data = canonical_json_bytes(dict(state_value))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".extractor-state.json.tmp-", dir=state_dir
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, state_dir / "extractor-state.json")
        _fsync_directory(state_dir)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


@contextmanager
def extractor_lock(
    root: Path,
    *,
    exclusive: bool,
    nonblocking: bool,
    create: bool,
) -> Iterator[bool]:
    """Acquire and yield a shared or exclusive advisory extractor lock."""
    path = root / "state" / "extractor.lock"
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    try:
        descriptor = _open_regular_nofollow(path, flags, 0o600)
    except OSError as exc:
        raise ExtractorError(f"cannot open extractor lock {path}: {exc}") from exc
    acquired = False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExtractorError("extractor lock is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ExtractorError("extractor lock permissions must be exactly 0600")
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if nonblocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        descriptor = _open_regular_nofollow(path, os.O_RDONLY)
    except OSError as exc:
        raise ExtractorError(f"cannot open JSONL file {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExtractorError(f"JSONL path is not a regular file: {path}")
        handle = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = -1
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.endswith("\n"):
                raise ExtractorError(f"unterminated JSONL row in {path}:{line_number}")
            if not line.strip():
                raise ExtractorError(f"blank JSONL row in {path}:{line_number}")
            try:
                value = strict_json_loads(line)
            except (ValueError, json.JSONDecodeError) as exc:
                raise ExtractorError(f"invalid JSONL in {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ExtractorError(f"JSONL row is not an object in {path}:{line_number}")
            rows.append(value)
    return rows


def _current_link_target(root: Path, *, required: bool) -> str | None:
    current = root / "current"
    try:
        info = current.lstat()
    except FileNotFoundError:
        if required:
            raise ExtractorError("current snapshot symlink is missing")
        return None
    except OSError as exc:
        raise ExtractorError(f"cannot inspect current snapshot symlink: {exc}") from exc
    if not stat.S_ISLNK(info.st_mode):
        raise ExtractorError("current must be a relative symlink")
    target = os.readlink(current)
    if not re.fullmatch(r"batches/[A-Za-z0-9._-]+", target):
        raise ExtractorError(f"current has an unsafe or non-relative target: {target!r}")
    target_path = root / target
    _require_real_directory(target_path, label="current snapshot")
    return target


def _current_batch_path(root: Path, *, required: bool = True) -> Path | None:
    target = _current_link_target(root, required=required)
    return root / target if target is not None else None


def _load_prior_posts(root: Path, state_value: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if state_value is None:
        if _current_link_target(root, required=False) is not None:
            raise ExtractorError("current exists while extractor state is uninitialised")
        return []
    batch = _current_batch_path(root)
    assert batch is not None
    expected_batch = str(state_value.get("last_successful_batch") or "")
    if batch.name != expected_batch:
        raise ExtractorError("extractor state and current snapshot disagree")
    problems = _validate_batch_directory(
        batch,
        require_immutable=True,
        expected_boundary=str(state_value.get("prospective_boundary") or ""),
    )
    if problems:
        raise ExtractorError(
            "current snapshot is incompatible or corrupt: " + "; ".join(problems)
        )
    posts = _load_jsonl(batch / "canonical-posts.jsonl")
    if any(
        post.get("schema_version") != SCHEMA_VERSION
        or post.get("derivation_parser_version") != PARSER_VERSION
        for post in posts
    ):
        raise ExtractorError("current snapshot contains mixed parser/schema versions")
    return posts


@dataclass(frozen=True)
class IncrementalParse:
    """Hold records, cache state, warnings, and incremental reuse counts."""

    records: tuple[LogRecord, ...]
    source_cache: dict[str, Any]
    earliest_source_timestamp: str | None
    latest_source_timestamp: str | None
    warnings: tuple[dict[str, Any], ...]
    parsed_source_hash_count: int
    reused_source_hash_count: int


def parse_incremental_sources(
    inventory: SourceInventory,
    prior_state: Mapping[str, Any] | None,
    *,
    cutoff: datetime,
) -> IncrementalParse:
    """Parse inventory sources using validated entries from the prior cache."""
    prior_cache = copy.deepcopy(
        dict((prior_state or {}).get("source_file_cache") or {})
    )
    cache: dict[str, Any] = {}
    records_by_hash: dict[str, list[LogRecord]] = {}
    output_records: list[LogRecord] = []
    parse_warnings: list[dict[str, Any]] = []
    parsed_hashes: set[str] = set()
    reused_hashes: set[str] = set()
    cutoff_text = str(format_utc(cutoff))

    for source in inventory.files:
        digest = source.content_sha256
        prior_entry = prior_cache.get(digest)
        entry = (
            copy.deepcopy(prior_entry)
            if isinstance(prior_entry, dict)
            and prior_entry.get("parser_version") == PARSER_VERSION
            else None
        )
        if entry is not None:
            cache[digest] = entry
        should_parse = not isinstance(entry, dict)
        if isinstance(entry, dict):
            processed_cutoff = str(entry.get("processed_cutoff") or "")
            latest_record = str(entry.get("latest_record_timestamp") or "")
            if cutoff_text > processed_cutoff and latest_record > processed_cutoff:
                should_parse = True
        if digest in records_by_hash:
            parsed = records_by_hash[digest]
            should_parse = False
        elif should_parse:
            parsed, warnings = parse_log_records(source.complete_data)
            records_by_hash[digest] = parsed
            parsed_hashes.add(digest)
            parse_warnings.extend(
                {
                    **warning,
                    "source_content_sha256": digest,
                }
                for warning in warnings
            )
            timestamps = [record.timestamp for record in parsed]
            entry = {
                "complete_line_count": source.complete_line_count,
                "earliest_record_timestamp": min(timestamps) if timestamps else None,
                "incomplete_trailing_line_ignored": source.incomplete_trailing_line_ignored,
                "latest_record_timestamp": max(timestamps) if timestamps else None,
                "parser_version": PARSER_VERSION,
                "processed_cutoff": cutoff_text,
                "record_count": len(parsed),
                "size": source.size,
            }
            cache[digest] = entry
        else:
            parsed = []
            reused_hashes.add(digest)
        assert isinstance(cache.get(digest), dict)
        cached_entry = cache[digest]
        prior_names = cached_entry.get("last_seen_basenames")
        names = {str(value) for value in prior_names or [] if str(value)}
        names.add(source.basename)
        cached_entry["last_seen_basenames"] = sorted(names)
        cached_entry["last_seen_modification_time_ns"] = source.modification_time_ns
        if digest in records_by_hash:
            output_records.extend(
                record
                for record in records_by_hash[digest]
                if record.timestamp_value <= cutoff
            )

    current_entries = [cache[source.content_sha256] for source in inventory.files]
    earliest = min(
        (
            str(entry.get("earliest_record_timestamp"))
            for entry in current_entries
            if entry.get("earliest_record_timestamp")
        ),
        default=None,
    )
    latest = max(
        (
            str(entry.get("latest_record_timestamp"))
            for entry in current_entries
            if entry.get("latest_record_timestamp")
        ),
        default=None,
    )
    parse_warnings.sort(key=lambda item: canonical_json_bytes(item, newline=False))
    return IncrementalParse(
        records=tuple(output_records),
        source_cache=cache,
        earliest_source_timestamp=earliest,
        latest_source_timestamp=latest,
        warnings=tuple(parse_warnings),
        parsed_source_hash_count=len(parsed_hashes),
        reused_source_hash_count=len(reused_hashes),
    )


def _read_repository_commit(project_dir: Path) -> str | None:
    """Read Git metadata directly; never invoke a command or network transport."""
    dot_git = project_dir / ".git"
    try:
        if dot_git.is_file():
            text = dot_git.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir: "):
                return None
            git_dir = (dot_git.parent / text[8:]).resolve()
        elif dot_git.is_dir():
            git_dir = dot_git
        else:
            return None
        head_text = (git_dir / "HEAD").read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", head_text):
            return head_text.lower()
        if not head_text.startswith("ref: "):
            return None
        reference = head_text[5:]
        candidates = [git_dir / reference]
        common_file = git_dir / "commondir"
        common_dir = git_dir
        if common_file.is_file():
            common_dir = (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
            candidates.append(common_dir / reference)
        for candidate in candidates:
            if candidate.is_file():
                value = candidate.read_text(encoding="ascii").strip()
                if re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
                    return value.lower()
        packed = common_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="ascii").splitlines():
                if line.startswith(("#", "^")) or " " not in line:
                    continue
                value, name = line.split(" ", 1)
                if name == reference and re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
                    return value.lower()
    except (OSError, UnicodeError, RuntimeError):
        return None
    return None


def _extractor_code_provenance(
    script_path: Path | None = None,
) -> dict[str, str | None]:
    """Describe the exact extractor source file without invoking Git."""
    try:
        script = (script_path or Path(__file__)).resolve(strict=True)
    except OSError as exc:
        raise ExtractorError(f"cannot resolve extractor script path: {exc}") from exc
    repository_root = script.parent.parent
    try:
        relative_path = script.relative_to(repository_root).as_posix()
    except ValueError as exc:
        raise ExtractorError("extractor script is outside its repository root") from exc
    if relative_path != "tools/extract_prospective_conversations.py":
        raise ExtractorError(
            "extractor script repository path is unexpected: " + relative_path
        )
    try:
        script_sha256 = sha256_file(script)
    except OSError as exc:
        raise ExtractorError(f"cannot hash extractor script: {exc}") from exc
    return {
        "extractor_repository_commit_sha": _read_repository_commit(
            repository_root
        ),
        "extractor_script_path": relative_path,
        "extractor_script_sha256": script_sha256,
    }


def _snapshot_hash(
    canonical_posts_hash: str,
    conversations_hash: str,
    review_candidates_hash: str,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "canonical_posts_sha256": canonical_posts_hash,
                "conversations_sha256": conversations_hash,
                "extractor_version": EXTRACTOR_VERSION,
                "parser_version": PARSER_VERSION,
                "review_candidates_sha256": review_candidates_hash,
                "schema_version": SCHEMA_VERSION,
            },
            newline=False,
        )
    )


def _count_snapshot(
    posts: Sequence[Mapping[str, Any]],
    conversations: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    source_count: int,
) -> dict[str, int]:
    return {
        "canonical_post_count": len(posts),
        "open_conversation_count": sum(
            row.get("activity_status") == "open" for row in conversations
        ),
        "prospective_eligible_conversation_count": sum(
            row.get("prospective_status") == "eligible" for row in conversations
        ),
        "quiescent_conversation_count": sum(
            row.get("activity_status") == "quiescent" for row in conversations
        ),
        "reconstructed_conversation_count": len(conversations),
        "review_candidate_count": len(candidates),
        "source_file_count": source_count,
    }


def _write_file(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    descriptor = _open_regular_nofollow(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode
    )
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _set_current(root: Path, target: str) -> None:
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise ExtractorError("refusing to replace non-symlink current path")
    temporary = root / f".current.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    try:
        os.symlink(target, temporary)
        os.replace(temporary, current)
        _fsync_directory(root)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore_current(root: Path, previous_target: str | None, new_target: str) -> None:
    current = root / "current"
    if previous_target is not None:
        _set_current(root, previous_target)
        return
    try:
        if current.is_symlink() and os.readlink(current) == new_target:
            current.unlink()
            _fsync_directory(root)
    except FileNotFoundError:
        pass


def _make_tree_read_only(directory: Path) -> None:
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ExtractorError(f"immutable output contains a non-regular file: {path}")
        os.chmod(path, 0o400, follow_symlinks=False)
    os.chmod(directory, 0o500, follow_symlinks=False)
    _fsync_directory(directory.parent)


def _remove_new_tree(directory: Path) -> None:
    """Remove only a just-created unpublished tree during exception rollback."""
    if directory.parent.name not in {"batches", "review-packs"}:
        raise ExtractorError(f"refusing unsafe rollback target: {directory}")
    if not directory.name or directory.name.startswith("."):
        raise ExtractorError(f"refusing unsafe rollback target: {directory}")
    if not directory.exists():
        return
    os.chmod(directory, 0o700, follow_symlinks=False)
    for path in directory.iterdir():
        if path.is_file() and not path.is_symlink():
            os.chmod(path, 0o600, follow_symlinks=False)
    shutil.rmtree(directory)
    _fsync_directory(directory.parent)


@dataclass(frozen=True)
class RetentionResult:
    """Describe retained storage, pruning, validation counts, and warnings."""

    pruned_batch_ids: tuple[str, ...]
    retained_batch_count: int
    retained_automatic_bytes: int
    protected_automatic_bytes: int
    review_pack_bytes: int
    retention_full_validation_batch_count: int
    retention_lightweight_protected_batch_count: int
    total_extractor_bytes: int
    storage_warnings: tuple[str, ...] = ()

    def as_state_fields(self) -> dict[str, Any]:
        """Return retention metrics suitable for persisted extractor state."""
        return {
            "pruned_batch_ids": list(self.pruned_batch_ids),
            "protected_automatic_bytes": self.protected_automatic_bytes,
            "retained_automatic_bytes": self.retained_automatic_bytes,
            "retained_batch_count": self.retained_batch_count,
            "retention_full_validation_batch_count": (
                self.retention_full_validation_batch_count
            ),
            "retention_lightweight_protected_batch_count": (
                self.retention_lightweight_protected_batch_count
            ),
            "review_pack_bytes": self.review_pack_bytes,
            "total_extractor_bytes": self.total_extractor_bytes,
        }


@dataclass(frozen=True)
class RetentionBatchMetadata:
    """Hold trusted creation time and size metadata for an automatic batch."""

    path: Path
    created_at: datetime
    size_bytes: int


def _path_tree_bytes(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or stat.S_ISREG(info.st_mode):
            total += int(info.st_size)
        elif stat.S_ISDIR(info.st_mode):
            total += int(info.st_size)
            stack.extend(current.iterdir())
        else:
            raise ExtractorError(f"unsupported filesystem object in output root: {current}")
    return total


def _read_retention_batch_metadata(
    batch: Path,
    *,
    expected_boundary: str,
) -> RetentionBatchMetadata:
    """Read only trustworthy batch metadata; never open corpus JSONL files."""
    try:
        info = batch.lstat()
    except OSError as exc:
        raise ExtractorError(f"cannot inspect automatic batch {batch}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExtractorError(f"automatic batch is not a non-symlink directory: {batch}")
    if not BATCH_ID_RE.fullmatch(batch.name):
        raise ExtractorError(f"automatic batch name is invalid: {batch.name}")
    if stat.S_IMODE(info.st_mode) != 0o500:
        raise ExtractorError(f"automatic batch directory mode is not 0500: {batch}")
    try:
        names = sorted(path.name for path in batch.iterdir())
    except OSError as exc:
        raise ExtractorError(f"cannot enumerate automatic batch {batch}: {exc}") from exc
    if names != sorted(BATCH_FILES):
        raise ExtractorError(f"automatic batch file set is incorrect: {batch}")
    manifest_path = batch / "manifest.json"
    try:
        manifest_info = manifest_path.lstat()
    except OSError as exc:
        raise ExtractorError(f"cannot inspect batch manifest {manifest_path}: {exc}") from exc
    if stat.S_ISLNK(manifest_info.st_mode) or not stat.S_ISREG(manifest_info.st_mode):
        raise ExtractorError(f"batch manifest is not a regular file: {manifest_path}")
    if stat.S_IMODE(manifest_info.st_mode) != 0o400:
        raise ExtractorError(f"batch manifest mode is not 0400: {manifest_path}")
    manifest = _strict_read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ExtractorError(f"batch manifest is not an object: {batch}")
    if manifest_path.read_bytes() != canonical_json_bytes(manifest):
        raise ExtractorError(f"batch manifest is not in canonical JSON form: {batch}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ExtractorError(f"unsupported batch manifest schema: {batch}")
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        raise ExtractorError(f"batch extractor version mismatch: {batch}")
    if manifest.get("parser_version") != PARSER_VERSION:
        raise ExtractorError(f"batch parser version mismatch: {batch}")
    if manifest.get("prospective_boundary") != expected_boundary:
        raise ExtractorError(f"batch prospective boundary mismatch: {batch}")
    created = parse_aware_timestamp(
        str(manifest.get("creation_timestamp") or ""),
        option=f"batch {batch.name} creation timestamp",
    )
    previous_batch = manifest.get("previous_batch_id")
    if previous_batch is not None and not BATCH_ID_RE.fullmatch(str(previous_batch)):
        raise ExtractorError(f"batch previous-batch identity is invalid: {batch}")
    snapshot_hash = str(manifest.get("canonical_snapshot_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash):
        raise ExtractorError(f"batch snapshot identity is invalid: {batch}")
    if not batch.name.endswith(f"-{snapshot_hash[:12]}"):
        raise ExtractorError(f"batch name disagrees with snapshot identity: {batch}")
    return RetentionBatchMetadata(
        path=batch,
        created_at=created,
        size_bytes=_path_tree_bytes(batch),
    )


def _read_retention_review_pack_provenance(
    pack: Path,
    *,
    expected_boundary: str,
) -> str:
    """Read only immutable review-pack provenance needed for retention."""
    try:
        info = pack.lstat()
    except OSError as exc:
        raise ExtractorError(f"cannot inspect review pack {pack}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExtractorError(f"review pack is not a non-symlink directory: {pack}")
    if stat.S_IMODE(info.st_mode) != 0o500:
        raise ExtractorError(f"review pack directory mode is not 0500: {pack}")
    try:
        names = sorted(path.name for path in pack.iterdir())
    except OSError as exc:
        raise ExtractorError(f"cannot enumerate review pack {pack}: {exc}") from exc
    if names != sorted(PACK_FILES):
        raise ExtractorError(f"review pack file set is incorrect: {pack}")
    manifest_path = pack / "manifest.json"
    try:
        manifest_info = manifest_path.lstat()
    except OSError as exc:
        raise ExtractorError(f"cannot inspect review-pack manifest {manifest_path}: {exc}") from exc
    if stat.S_ISLNK(manifest_info.st_mode) or not stat.S_ISREG(manifest_info.st_mode):
        raise ExtractorError(f"review-pack manifest is not a regular file: {manifest_path}")
    if stat.S_IMODE(manifest_info.st_mode) != 0o400:
        raise ExtractorError(f"review-pack manifest mode is not 0400: {manifest_path}")
    manifest = _strict_read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ExtractorError(f"review-pack manifest is not an object: {pack}")
    if manifest_path.read_bytes() != canonical_json_bytes(manifest):
        raise ExtractorError(f"review-pack manifest is not canonical JSON: {pack}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ExtractorError(f"unsupported review-pack schema: {pack}")
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        raise ExtractorError(f"review-pack extractor version mismatch: {pack}")
    if manifest.get("parser_version") != PARSER_VERSION:
        raise ExtractorError(f"review-pack parser version mismatch: {pack}")
    if manifest.get("prospective_boundary") != expected_boundary:
        raise ExtractorError(f"review-pack prospective boundary mismatch: {pack}")
    if manifest.get("pack_name") != pack.name:
        raise ExtractorError(f"review-pack identity mismatch: {pack}")
    parse_aware_timestamp(
        str(manifest.get("creation_timestamp") or ""),
        option=f"review pack {pack.name} creation timestamp",
    )
    parse_aware_timestamp(
        str(manifest.get("source_batch_creation_timestamp") or ""),
        option=f"review pack {pack.name} source batch creation timestamp",
    )
    source_batch = str(manifest.get("source_batch_id") or "")
    if not BATCH_ID_RE.fullmatch(source_batch):
        raise ExtractorError(f"review-pack source batch is invalid: {pack}")
    return source_batch


def _review_pack_batch_references(
    root: Path,
    *,
    expected_boundary: str,
) -> tuple[set[str], int]:
    references: set[str] = set()
    total_bytes = 0
    for pack in sorted((root / "review-packs").iterdir(), key=lambda item: item.name):
        try:
            source_batch = _read_retention_review_pack_provenance(
                pack, expected_boundary=expected_boundary
            )
        except ExtractorError as exc:
            raise ExtractorError(
                "review-pack provenance is invalid; retention is blocked: "
                + str(exc)
            ) from exc
        references.add(source_batch)
        total_bytes += _path_tree_bytes(pack)
    return references, total_bytes


def measure_retained_storage(
    root: Path,
    *,
    expected_boundary: str | None = None,
) -> RetentionResult:
    """Measure retained batches, review packs, and protected storage bytes."""
    batches = sorted(
        (
            path
            for path in (root / "batches").iterdir()
            if BATCH_ID_RE.fullmatch(path.name)
        ),
        key=lambda path: path.name,
    )
    packs = sorted((root / "review-packs").iterdir(), key=lambda path: path.name)
    warnings: list[str] = []
    references: set[str] = set()
    if expected_boundary:
        for pack in packs:
            try:
                references.add(
                    _read_retention_review_pack_provenance(
                        pack, expected_boundary=expected_boundary
                    )
                )
            except ExtractorError:
                warnings.append(
                    f"storage_protection_reference_unavailable:{pack.name}"
                )
    current_batch: str | None = None
    try:
        current_target = _current_link_target(root, required=False)
        current_batch = Path(current_target).name if current_target else None
    except ExtractorError:
        warnings.append("storage_current_reference_unavailable")
    sizes = {path.name: _path_tree_bytes(path) for path in batches}
    protected_names = set(references)
    if current_batch:
        protected_names.add(current_batch)
    return RetentionResult(
        pruned_batch_ids=(),
        retained_batch_count=len(batches),
        retained_automatic_bytes=sum(sizes.values()),
        protected_automatic_bytes=sum(
            size for name, size in sizes.items() if name in protected_names
        ),
        review_pack_bytes=sum(_path_tree_bytes(path) for path in packs),
        retention_full_validation_batch_count=0,
        retention_lightweight_protected_batch_count=len(batches),
        total_extractor_bytes=_path_tree_bytes(root),
        storage_warnings=tuple(sorted(set(warnings))),
    )


def _delete_retained_batch(batch: Path) -> None:
    """Delete one already-validated automatic batch without following anything."""
    if batch.parent.name != "batches" or not BATCH_ID_RE.fullmatch(batch.name):
        raise ExtractorError(f"refusing unsafe retention target: {batch}")
    info = batch.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExtractorError(f"refusing non-directory retention target: {batch}")
    names = sorted(path.name for path in batch.iterdir())
    if names != sorted(BATCH_FILES):
        raise ExtractorError(f"refusing batch with unexpected contents: {batch}")
    os.chmod(batch, 0o700, follow_symlinks=False)
    for name in BATCH_FILES:
        path = batch / name
        item = path.lstat()
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
            raise ExtractorError(f"refusing unsafe batch member: {path}")
        os.chmod(path, 0o600, follow_symlinks=False)
        path.unlink()
    batch.rmdir()
    _fsync_directory(batch.parent)


def apply_batch_retention(
    root: Path,
    *,
    now: datetime,
    expected_boundary: str,
    projected_batch_bytes: int = 0,
) -> RetentionResult:
    """Apply time and byte retention using lightweight protected inventory."""
    if projected_batch_bytes < 0:
        raise ExtractorError("projected batch bytes must not be negative")
    current_target = _current_link_target(root, required=False)
    current_batch = Path(current_target).name if current_target else None
    review_references, review_bytes = _review_pack_batch_references(
        root, expected_boundary=expected_boundary
    )
    batch_rows: list[RetentionBatchMetadata] = []
    for batch in sorted((root / "batches").iterdir(), key=lambda item: item.name):
        if not BATCH_ID_RE.fullmatch(batch.name):
            raise ExtractorError(
                f"malformed or temporary batch blocks retention: {batch.name}"
            )
        try:
            metadata = _read_retention_batch_metadata(
                batch, expected_boundary=expected_boundary
            )
        except ExtractorError as exc:
            raise ExtractorError(
                "invalid automatic batch blocks retention: " + str(exc)
            ) from exc
        batch_rows.append(metadata)
    missing_references = review_references - {row.path.name for row in batch_rows}
    if missing_references:
        raise ExtractorError(
            "review pack references missing automatic batch; retention is blocked"
        )

    permanently_protected = set(review_references)
    if current_batch:
        permanently_protected.add(current_batch)
    policy_protected = set(permanently_protected)
    recent_floor = now - RECENT_BATCH_RETENTION
    daily_floor = now - DAILY_BATCH_RETENTION
    older_daily: dict[str, RetentionBatchMetadata] = {}
    for row in batch_rows:
        if row.created_at >= recent_floor:
            policy_protected.add(row.path.name)
        elif row.created_at >= daily_floor:
            day = row.created_at.strftime("%Y-%m-%d")
            previous = older_daily.get(day)
            if previous is None or (row.created_at, row.path.name) > (
                previous.created_at,
                previous.path.name,
            ):
                older_daily[day] = row
    policy_protected.update(row.path.name for row in older_daily.values())

    candidates = [
        row for row in batch_rows if row.path.name not in policy_protected
    ]
    candidate_names = {row.path.name for row in candidates}
    retained_rows = [
        row for row in batch_rows if row.path.name not in candidate_names
    ]
    retained_bytes = sum(row.size_bytes for row in retained_rows)
    projected_total = retained_bytes + projected_batch_bytes
    if projected_total > MAX_AUTOMATIC_BATCH_BYTES:
        budget_candidates = sorted(
            (
                row
                for row in retained_rows
                if row.path.name not in permanently_protected
            ),
            key=lambda row: (row.created_at, row.path.name),
        )
        for row in budget_candidates:
            candidates.append(row)
            candidate_names.add(row.path.name)
            retained_bytes -= row.size_bytes
            projected_total = retained_bytes + projected_batch_bytes
            if projected_total <= MAX_AUTOMATIC_BATCH_BYTES:
                break

    protected_bytes = sum(
        row.size_bytes
        for row in batch_rows
        if row.path.name in permanently_protected
    )
    if projected_batch_bytes and (
        protected_bytes + projected_batch_bytes > MAX_AUTOMATIC_BATCH_BYTES
    ):
        usage = shutil.disk_usage(root)
        raise ExtractorError(
            "protected automatic batches plus projected snapshot exceed budget: "
            f"filesystem_total_bytes={int(usage.total)} "
            f"filesystem_used_bytes={int(usage.used)} "
            f"filesystem_free_bytes={int(usage.free)} "
            f"projected_batch_bytes={projected_batch_bytes} "
            f"minimum_free_bytes={MIN_FILESYSTEM_FREE_BYTES} "
            f"required_free_bytes={projected_batch_bytes + MIN_FILESYSTEM_FREE_BYTES} "
            f"automatic_batch_budget_bytes={MAX_AUTOMATIC_BATCH_BYTES} "
            f"retained_automatic_bytes={retained_bytes} "
            f"protected_automatic_bytes={protected_bytes}"
        )

    unique_candidates = sorted(
        {row.path.name: row for row in candidates}.values(),
        key=lambda row: (row.created_at, row.path.name),
    )
    for row in unique_candidates:
        if row.path.name in permanently_protected:
            raise ExtractorError(
                f"internal retention protection failure: {row.path.name}"
            )
    candidate_problems: list[str] = []
    for row in unique_candidates:
        problems = _validate_batch_directory(
            row.path,
            require_immutable=True,
            expected_boundary=expected_boundary,
        )
        candidate_problems.extend(problems)
    if candidate_problems:
        raise ExtractorError(
            "invalid automatic deletion candidate blocks retention: "
            + "; ".join(candidate_problems)
        )
    for row in unique_candidates:
        _delete_retained_batch(row.path)

    deleted_names = {row.path.name for row in unique_candidates}
    retained_rows = [
        row for row in batch_rows if row.path.name not in deleted_names
    ]
    automatic_bytes = sum(row.size_bytes for row in retained_rows)
    return RetentionResult(
        pruned_batch_ids=tuple(row.path.name for row in unique_candidates),
        retained_batch_count=len(retained_rows),
        retained_automatic_bytes=automatic_bytes,
        protected_automatic_bytes=protected_bytes,
        review_pack_bytes=review_bytes,
        retention_full_validation_batch_count=len(unique_candidates),
        retention_lightweight_protected_batch_count=len(retained_rows),
        total_extractor_bytes=_path_tree_bytes(root),
    )


def _build_extraction_report(
    *,
    boundary: str,
    cutoff: str,
    counts: Mapping[str, int],
    coverage: bool,
    source_lag_seconds: int,
    warnings: Sequence[str],
) -> bytes:
    lines = [
        "# Prospective conversation extraction report",
        "",
        "This snapshot is a private, descriptive research corpus. Collection is not a",
        "finding that any reply was defective and makes no repair, pass, publish, or",
        "suppress decision.",
        "",
        f"- Prospective boundary: `{boundary}`",
        f"- Scan cut-off: `{cutoff}`",
        f"- Source coverage reaches boundary: `{str(coverage).lower()}`",
        f"- Retained source lag: `{source_lag_seconds}` seconds",
        f"- Canonical posts: `{counts['canonical_post_count']}`",
        f"- Reconstructed conversations: `{counts['reconstructed_conversation_count']}`",
        f"- Prospective-eligible conversations: `{counts['prospective_eligible_conversation_count']}`",
        f"- Review candidates: `{counts['review_candidate_count']}`",
        f"- Open conversations: `{counts['open_conversation_count']}`",
        f"- Quiescent conversations: `{counts['quiescent_conversation_count']}`",
        "",
        "No model, API, provider, or X call is used by this extractor.",
    ]
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _walk_forbidden_keys(value: Any, *, location: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if lowered in FORBIDDEN_OUTPUT_KEYS:
                errors.append(f"forbidden key {key!r} at {location}")
            errors.extend(_walk_forbidden_keys(child, location=f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_walk_forbidden_keys(child, location=f"{location}[{index}]"))
    return errors


def _is_canonical_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = parse_optional_timestamp(value)
    return parsed is not None and format_utc(parsed) == value


def _is_lower_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_reply_visual_context_summary_shape(
    value: Any,
    *,
    location: str,
    include_post_id: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"reply visual context summary is not an object at {location}"]
    expected_fields = set(REPLY_VISUAL_CONTEXT_SUMMARY_FIELDS)
    if include_post_id:
        expected_fields.add("post_id")
    if set(value) != expected_fields:
        errors.append(
            f"reply visual context summary fields are invalid at {location}"
        )
    if include_post_id and (
        not isinstance(value.get("post_id"), str) or not value.get("post_id")
    ):
        errors.append(f"reply visual context summary post ID is invalid at {location}")
    for field in (
        "analysis_attempt_count",
        "distinct_successful_description_count",
        "native_photo_count_max",
        "omitted_media_observation_count",
        "omitted_visual_event_count",
        "successful_analysis_count",
        "visual_event_count",
    ):
        field_value = value.get(field)
        if type(field_value) is not int or field_value < 0:
            errors.append(f"{field} is invalid at {location}")
    if (
        type(value.get("native_photo_count_max")) is int
        and value["native_photo_count_max"] > MAX_REPLY_VISUAL_REPORTED_IMAGES
    ):
        errors.append(f"native_photo_count_max is invalid at {location}")
    for field in ("analysis_history_complete", "collection_history_complete"):
        if type(value.get(field)) is not bool:
            errors.append(f"{field} is invalid at {location}")
    if (
        value.get("analysis_observation_status")
        not in REPLY_VISUAL_ANALYSIS_OBSERVATION_STATUSES
    ):
        errors.append(f"analysis observation status is invalid at {location}")
    latest_analysis_status = value.get("latest_analysis_status")
    if (
        latest_analysis_status is not None
        and latest_analysis_status not in REPLY_VISUAL_DESCRIPTION_STATUSES
    ):
        errors.append(f"latest analysis status is invalid at {location}")
    latest_collection_status = value.get("latest_collection_status")
    if latest_collection_status is not None and latest_collection_status not in {
        "supplied",
        "unavailable",
    }:
        errors.append(f"latest collection status is invalid at {location}")
    schema_versions = value.get("analysis_schema_versions")
    if (
        not isinstance(schema_versions, list)
        or any(
            type(item) is not int
            or item <= 0
            or item > MAX_REPLY_VISUAL_SCHEMA_VERSION
            for item in schema_versions
        )
        or schema_versions != sorted(set(schema_versions))
    ):
        errors.append(f"analysis schema versions are invalid at {location}")
    hashes = value.get("successful_description_sha256s")
    if (
        not isinstance(hashes, list)
        or any(not _is_lower_sha256(item) for item in hashes)
        or hashes != sorted(set(hashes))
    ):
        errors.append(f"successful description hashes are invalid at {location}")
    return errors


def _validate_reply_visual_metadata(
    value: Mapping[str, Any], *, location: str
) -> list[str]:
    """Validate one post/turn's complete version-4 visual metadata."""
    errors: list[str] = []
    missing_fields = REPLY_VISUAL_METADATA_FIELDS - set(value)
    if missing_fields:
        errors.append(f"reply visual metadata fields are missing at {location}")

    media = value.get("reply_media_context_observations")
    media_valid_for_derivation = isinstance(media, list)
    if not isinstance(media, list):
        errors.append(f"reply media context observations are not a list at {location}")
        media = []
    elif len(media) > MAX_REPLY_MEDIA_CONTEXT_OBSERVATIONS:
        errors.append(f"reply media context observation bound is exceeded at {location}")
    media_fingerprints: list[str] = []
    media_rows_are_objects = all(isinstance(item, dict) for item in media)
    for index, item in enumerate(media):
        item_location = f"{location}.reply_media_context_observations[{index}]"
        if not isinstance(item, dict):
            errors.append(f"reply media context observation is not an object at {item_location}")
            media_valid_for_derivation = False
            continue
        if set(item) != REPLY_MEDIA_CONTEXT_OBSERVATION_FIELDS:
            errors.append(f"reply media context observation fields are invalid at {item_location}")
        if not _is_canonical_utc_timestamp(item.get("observed_at")):
            errors.append(f"reply media context observation time is invalid at {item_location}")
        fingerprint = item.get("record_fingerprint")
        if not _is_lower_sha256(fingerprint):
            errors.append(f"reply media context fingerprint is invalid at {item_location}")
        else:
            media_fingerprints.append(fingerprint)
        if item.get("lane") not in REPLY_VISUAL_LANES:
            errors.append(f"reply media context lane is invalid at {item_location}")
        if item.get("mode") != "multimodal":
            errors.append(f"reply media context mode is invalid at {item_location}")
        if item.get("status") not in {"supplied", "unavailable"}:
            errors.append(f"reply media context status is invalid at {item_location}")
        photo_count = item.get("photo_count")
        if (
            type(photo_count) is not int
            or photo_count <= 0
            or photo_count > MAX_REPLY_VISUAL_REPORTED_IMAGES
        ):
            errors.append(f"reply media context photo count is invalid at {item_location}")
    if len(media_fingerprints) != len(set(media_fingerprints)):
        errors.append(f"reply media context fingerprints are duplicated at {location}")
    if media_rows_are_objects and media != sorted(
        media, key=_reply_observation_order_key
    ):
        errors.append(f"reply media context ordering is invalid at {location}")

    visual_events = value.get("reply_visual_description_attempts")
    visual_valid_for_derivation = isinstance(visual_events, list)
    if not isinstance(visual_events, list):
        errors.append(f"reply visual event history is not a list at {location}")
        visual_events = []
    elif len(visual_events) > MAX_REPLY_VISUAL_DESCRIPTION_ATTEMPTS:
        errors.append(f"reply visual event history bound is exceeded at {location}")
    visual_fingerprints: list[str] = []
    visual_rows_are_objects = all(isinstance(item, dict) for item in visual_events)
    for index, item in enumerate(visual_events):
        item_location = f"{location}.reply_visual_description_attempts[{index}]"
        if not isinstance(item, dict):
            errors.append(f"reply visual event is not an object at {item_location}")
            visual_valid_for_derivation = False
            continue
        if set(item) != REPLY_VISUAL_DESCRIPTION_OBSERVATION_FIELDS:
            errors.append(f"reply visual event fields are invalid at {item_location}")
        if not _is_canonical_utc_timestamp(item.get("observed_at")):
            errors.append(f"reply visual event time is invalid at {item_location}")
        fingerprint = item.get("record_fingerprint")
        if not _is_lower_sha256(fingerprint):
            errors.append(f"reply visual event fingerprint is invalid at {item_location}")
        else:
            visual_fingerprints.append(fingerprint)
        if _validated_reply_visual_description_metadata(
            item,
            normalise_lane_value=False,
            allow_empty_hash=False,
        ) is None:
            errors.append(f"reply visual event contract is invalid at {item_location}")
    if len(visual_fingerprints) != len(set(visual_fingerprints)):
        errors.append(f"reply visual event fingerprints are duplicated at {location}")
    if visual_rows_are_objects and visual_events != sorted(
        visual_events, key=_reply_observation_order_key
    ):
        errors.append(f"reply visual event ordering is invalid at {location}")

    media_other = value.get("reply_media_context_other_count")
    if type(media_other) is not int or media_other < 0:
        errors.append(f"reply media context overflow count is invalid at {location}")
        media_valid_for_derivation = False
    elif (
        media_other > 0
        and isinstance(media, list)
        and len(media) < MAX_REPLY_MEDIA_CONTEXT_OBSERVATIONS
    ):
        errors.append(f"reply media context overflow count is inconsistent at {location}")
    visual_other = value.get("reply_visual_description_other_count")
    if type(visual_other) is not int or visual_other < 0:
        errors.append(f"reply visual event overflow count is invalid at {location}")
        visual_valid_for_derivation = False
    elif (
        visual_other > 0
        and isinstance(visual_events, list)
        and len(visual_events) < MAX_REPLY_VISUAL_DESCRIPTION_ATTEMPTS
    ):
        errors.append(f"reply visual event overflow count is inconsistent at {location}")

    summary = value.get("reply_visual_context_summary")
    errors.extend(
        _validate_reply_visual_context_summary_shape(
            summary,
            location=f"{location}.reply_visual_context_summary",
        )
    )
    if (
        isinstance(summary, dict)
        and media_valid_for_derivation
        and visual_valid_for_derivation
    ):
        expected_summary = _derive_reply_visual_context_summary(value)
        if summary != expected_summary:
            errors.append(f"reply visual context summary is inconsistent at {location}")
    return errors


def _reply_visual_metadata_matches(
    value: Mapping[str, Any], canonical_post: Mapping[str, Any]
) -> bool:
    return all(
        field in value
        and field in canonical_post
        and value[field] == canonical_post[field]
        for field in REPLY_VISUAL_METADATA_FIELDS
    )


def _validate_batch_directory(
    batch: Path,
    *,
    require_immutable: bool,
    expected_boundary: str | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        info = batch.lstat()
    except OSError as exc:
        return [f"cannot inspect batch {batch}: {exc}"]
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return [f"batch is not a non-symlink directory: {batch}"]
    if require_immutable and stat.S_IMODE(info.st_mode) != 0o500:
        errors.append(f"batch directory mode is not 0500: {batch}")
    names = sorted(path.name for path in batch.iterdir())
    if names != sorted(BATCH_FILES):
        errors.append(f"batch file set is incorrect in {batch}")
        return errors
    for name in BATCH_FILES:
        path = batch / name
        try:
            item_info = path.lstat()
        except OSError as exc:
            errors.append(f"cannot inspect {path}: {exc}")
            continue
        if stat.S_ISLNK(item_info.st_mode) or not stat.S_ISREG(item_info.st_mode):
            errors.append(f"batch output is not a regular file: {path}")
        if require_immutable and stat.S_IMODE(item_info.st_mode) != 0o400:
            errors.append(f"batch output mode is not 0400: {path}")
    if errors:
        return errors
    try:
        manifest = _strict_read_json(batch / "manifest.json")
        source_manifest = _strict_read_json(batch / "source-manifest.json")
        status_value = _strict_read_json(batch / "status.json")
    except ExtractorError as exc:
        return [str(exc)]
    for label, value in (
        ("manifest", manifest),
        ("source manifest", source_manifest),
        ("status", status_value),
    ):
        if not isinstance(value, dict):
            errors.append(f"{label} is not a JSON object in {batch}")
    if errors:
        return errors
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported batch manifest schema in {batch}")
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        errors.append(f"batch extractor version mismatch in {batch}")
    if manifest.get("parser_version") != PARSER_VERSION:
        errors.append(f"batch parser version mismatch in {batch}")
    extractor_commit = manifest.get("extractor_repository_commit_sha")
    if extractor_commit is not None and (
        not isinstance(extractor_commit, str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", extractor_commit)
    ):
        errors.append(f"extractor repository commit is invalid in {batch}")
    if manifest.get("repository_commit_sha") != extractor_commit:
        errors.append(f"legacy repository commit alias is inconsistent in {batch}")
    if manifest.get("extractor_script_path") != (
        "tools/extract_prospective_conversations.py"
    ):
        errors.append(f"extractor script path is invalid in {batch}")
    extractor_script_sha256 = manifest.get("extractor_script_sha256")
    if not isinstance(extractor_script_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", extractor_script_sha256
    ):
        errors.append(f"extractor script hash is invalid in {batch}")
    if source_manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"source manifest schema mismatch in {batch}")
    if source_manifest.get("extractor_version") != EXTRACTOR_VERSION:
        errors.append(f"source manifest extractor version mismatch in {batch}")
    if source_manifest.get("parser_version") != PARSER_VERSION:
        errors.append(f"source manifest parser version mismatch in {batch}")
    if status_value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"batch status schema mismatch in {batch}")
    if status_value.get("extractor_version") != EXTRACTOR_VERSION:
        errors.append(f"batch status extractor version mismatch in {batch}")
    if status_value.get("parser_version") != PARSER_VERSION:
        errors.append(f"batch status parser version mismatch in {batch}")
    if require_immutable and not BATCH_ID_RE.fullmatch(batch.name):
        errors.append(f"batch directory name is invalid: {batch.name}")
    if (batch / "manifest.json").read_bytes() != canonical_json_bytes(manifest):
        errors.append(f"batch manifest is not in canonical JSON form in {batch}")
    if (batch / "source-manifest.json").read_bytes() != canonical_json_bytes(
        source_manifest
    ):
        errors.append(f"source manifest is not in canonical JSON form in {batch}")
    if (batch / "status.json").read_bytes() != canonical_json_bytes(status_value):
        errors.append(f"status is not in canonical JSON form in {batch}")
    boundary = manifest.get("prospective_boundary")
    try:
        parse_aware_timestamp(str(boundary or ""), option="batch prospective boundary")
    except ExtractorError as exc:
        errors.append(str(exc))
    if expected_boundary is not None and boundary != expected_boundary:
        errors.append(f"batch boundary disagrees with extractor state: {batch.name}")
    if source_manifest.get("prospective_boundary") != boundary:
        errors.append(f"source manifest boundary mismatch in {batch}")
    if status_value.get("prospective_boundary") != boundary:
        errors.append(f"status boundary mismatch in {batch}")
    hashes = manifest.get("output_file_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(BATCH_HASHED_FILES):
        errors.append(f"batch output hash set is incorrect in {batch}")
    else:
        for name in BATCH_HASHED_FILES:
            observed = sha256_file(batch / name)
            if hashes.get(name) != observed:
                errors.append(f"output hash mismatch for {batch.name}/{name}")
        if manifest.get("source_manifest_sha256") != hashes.get("source-manifest.json"):
            errors.append(f"source-manifest hash field mismatch in {batch}")
        expected_snapshot = _snapshot_hash(
            str(hashes.get("canonical-posts.jsonl") or ""),
            str(hashes.get("conversations.jsonl") or ""),
            str(hashes.get("review-candidates.jsonl") or ""),
        )
        if manifest.get("canonical_snapshot_sha256") != expected_snapshot:
            errors.append(f"canonical snapshot hash mismatch in {batch}")
    try:
        posts = _load_jsonl(batch / "canonical-posts.jsonl")
        conversations = _load_jsonl(batch / "conversations.jsonl")
        candidates = _load_jsonl(batch / "review-candidates.jsonl")
    except ExtractorError as exc:
        errors.append(str(exc))
        return errors
    source_files = source_manifest.get("source_files")
    if not isinstance(source_files, list):
        errors.append(f"source manifest source_files is not a list in {batch}")
    else:
        required_source_fields = {
            "absolute_path",
            "basename",
            "complete_line_count",
            "content_sha256",
            "device",
            "incomplete_trailing_line_ignored",
            "inode",
            "modification_time_ns",
            "size",
        }
        source_names: list[str] = []
        for source_row in source_files:
            if not isinstance(source_row, dict) or not required_source_fields <= set(
                source_row
            ):
                errors.append(f"source manifest row is incomplete in {batch}")
                continue
            basename = str(source_row.get("basename") or "")
            source_names.append(basename)
            if _source_number(basename) is None:
                errors.append(f"source manifest has an invalid basename in {batch}")
            if not re.fullmatch(r"[0-9a-f]{64}", str(source_row.get("content_sha256") or "")):
                errors.append(f"source manifest has an invalid content hash in {batch}")
            if Path(str(source_row.get("absolute_path") or "")).name != basename:
                errors.append(f"source manifest path/basename mismatch in {batch}")
        if source_names != [path.name for path in sorted(map(Path, source_names), key=source_sort_key)]:
            errors.append(f"source manifest ordering is invalid in {batch}")
    for name, values in (
        ("canonical-posts.jsonl", posts),
        ("conversations.jsonl", conversations),
        ("review-candidates.jsonl", candidates),
    ):
        for index, value in enumerate(values, start=1):
            for problem in _walk_forbidden_keys(value):
                errors.append(f"{batch.name}/{name}:{index}: {problem}")
    post_order = [_turn_order_key(row)[:2] for row in posts]
    if post_order != sorted(post_order):
        errors.append(f"canonical post ordering is invalid in {batch}")
    canonical_post_lookup: dict[str, Mapping[str, Any]] = {}
    for post in posts:
        post_id = str(post.get("post_id") or "")
        canonical_post_lookup.setdefault(post_id, post)
    for post in posts:
        post_id = str(post.get("post_id") or "")
        if post.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"canonical post schema mismatch for {post_id} in {batch}")
        if post.get("derivation_parser_version") != PARSER_VERSION:
            errors.append(f"canonical post parser mismatch for {post_id} in {batch}")
        errors.extend(
            _validate_reply_visual_metadata(
                post,
                location=f"canonical post {post_id} in {batch}",
            )
        )
        if "timestamp" in post:
            errors.append(f"ambiguous canonical timestamp retained for {post_id} in {batch}")
        if post.get("creation_time_source") not in {
            "structured_event",
            "x_snowflake",
            "unavailable",
        }:
            errors.append(f"invalid creation-time source for {post_id} in {batch}")
        if post.get("creation_time_source") == "unavailable":
            if post.get("created_at") is not None:
                errors.append(f"unavailable creation time has a value for {post_id}")
            if post.get("creation_time_confidence") != "unavailable":
                errors.append(
                    f"unavailable creation time has authoritative confidence for {post_id}"
                )
        elif parse_optional_timestamp(post.get("created_at")) is None:
            errors.append(f"authoritative creation time is missing for {post_id}")
        elif post.get("creation_time_confidence") != "high":
            errors.append(f"authoritative creation time lacks high confidence for {post_id}")
        conflict = post.get("creation_time_conflict")
        conflict_details = post.get("creation_time_conflicts")
        if type(conflict) is not bool:
            errors.append(f"creation-time conflict flag is not boolean for {post_id}")
        if not isinstance(conflict_details, list):
            errors.append(f"creation-time conflicts are not a list for {post_id}")
            conflict_details = []
        if conflict:
            if post.get("creation_time_source") == "structured_event":
                errors.append(
                    f"conflicted structured time remains authoritative for {post_id}"
                )
            if len(conflict_details) < 2:
                errors.append(f"creation-time conflict lacks observations for {post_id}")
            parsed_conflicts: list[datetime] = []
            snowflake_values: list[str] = []
            for detail in conflict_details:
                if not isinstance(detail, dict):
                    errors.append(
                        f"creation-time conflict observation is not an object for {post_id}"
                    )
                    continue
                source = detail.get("source")
                created_at = str(detail.get("created_at") or "")
                parsed_detail = parse_optional_timestamp(created_at)
                if parsed_detail is None:
                    errors.append(
                        f"creation-time conflict observation lacks a timestamp for {post_id}"
                    )
                else:
                    parsed_conflicts.append(parsed_detail)
                if source == "structured_event":
                    if not all(
                        isinstance(detail.get(field), str) and detail.get(field)
                        for field in (
                            "event_kind",
                            "field",
                            "observed_at",
                            "record_fingerprint",
                        )
                    ):
                        errors.append(
                            f"structured creation-time conflict provenance is incomplete for {post_id}"
                        )
                    if parse_optional_timestamp(detail.get("observed_at")) is None:
                        errors.append(
                            f"structured creation-time conflict observation time is invalid for {post_id}"
                        )
                    if not re.fullmatch(
                        r"[0-9a-f]{64}", str(detail.get("record_fingerprint") or "")
                    ):
                        errors.append(
                            f"structured creation-time conflict fingerprint is invalid for {post_id}"
                        )
                elif source == "x_snowflake":
                    snowflake_values.append(created_at)
                    if detail.get("post_id_field") != "post_id":
                        errors.append(
                            f"Snowflake creation-time conflict provenance is invalid for {post_id}"
                        )
                else:
                    errors.append(
                        f"creation-time conflict source is invalid for {post_id}"
                    )
            if not any(
                abs((left - right).total_seconds()) > 1
                for index, left in enumerate(parsed_conflicts)
                for right in parsed_conflicts[index + 1 :]
            ):
                errors.append(
                    f"creation-time conflict observations do not materially disagree for {post_id}"
                )
            if post.get("creation_time_source") == "x_snowflake" and str(
                post.get("created_at") or ""
            ) not in snowflake_values:
                errors.append(
                    f"conflicted Snowflake result lacks matching provenance for {post_id}"
                )
        elif conflict_details:
            errors.append(
                f"creation-time conflict details exist without a conflict for {post_id}"
            )
        if post.get("publication_status") == "published" and post.get(
            "author_role"
        ) == "account":
            if post.get("publication_authority") not in {
                "structured_confirmation",
                "confirmed_receipt_promotion",
                "legacy_confirmed_sequence",
            }:
                errors.append(
                    f"published account post lacks authoritative publication source: {post_id}"
                )
            evidence = post.get("publication_evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(
                    f"published account post lacks publication evidence: {post_id}"
                )
            graph_authority = post.get("graph_evidence_authority")
            if post.get("lane") in {
                "quote_image",
                "daily_meme",
                "historical_context_reply",
            } or graph_authority:
                if graph_authority not in ACCOUNT_EVIDENCE_AUTHORITY_RANK:
                    errors.append(
                        f"account graph evidence authority is invalid for {post_id}"
                    )
                content_authority = post.get("content_evidence_authority")
                if post.get("text") is not None and (
                    content_authority not in ACCOUNT_EVIDENCE_AUTHORITY_RANK
                ):
                    errors.append(
                        f"account content evidence authority is invalid for {post_id}"
                    )
                if post.get("text") is not None and ACCOUNT_UNAVAILABLE_TEXT_WARNINGS & set(
                    post.get("warnings") or []
                ):
                    errors.append(
                        f"account post retains a stale unavailable-text warning: {post_id}"
                    )
                for conflict_field in (
                    "account_graph_conflicts",
                    "account_content_conflicts",
                ):
                    conflict_rows = post.get(conflict_field)
                    if (
                        not isinstance(conflict_rows, list)
                        or len(conflict_rows) > MAX_ACCOUNT_EVIDENCE_CONFLICTS
                        or not all(isinstance(row, dict) for row in conflict_rows)
                    ):
                        errors.append(
                            f"{conflict_field} is invalid for account post {post_id}"
                        )
                    elif conflict_rows != _merge_unique_objects([], conflict_rows):
                        errors.append(
                            f"{conflict_field} ordering is invalid for account post {post_id}"
                        )
                    overflow = post.get(f"{conflict_field[:-1]}_other_count")
                    if type(overflow) is not int or overflow < 0:
                        errors.append(
                            f"{conflict_field} overflow is invalid for account post {post_id}"
                        )
    conversation_order = [
        (str(row.get("start_time") or ""), str(row.get("conversation_key") or ""))
        for row in conversations
    ]
    if conversation_order != sorted(conversation_order):
        errors.append(f"conversation ordering is invalid in {batch}")
    for conversation in conversations:
        raw_turns = conversation.get("turns")
        if not isinstance(raw_turns, list):
            errors.append(
                f"conversation turns are not a list for {conversation.get('conversation_key')} in {batch}"
            )
            turns: list[Any] = []
        else:
            turns = raw_turns
        turn_rows_are_objects = all(isinstance(turn, dict) for turn in turns)
        if not turn_rows_are_objects:
            errors.append(
                f"conversation turns contain a non-object for {conversation.get('conversation_key')} in {batch}"
            )
        order = [
            _turn_order_key(turn) for turn in turns if isinstance(turn, dict)
        ]
        if turn_rows_are_objects and order != sorted(order):
            errors.append(
                f"turn ordering is invalid for {conversation.get('conversation_key')} in {batch}"
            )
        if conversation.get("prospective_status") not in {
            "eligible",
            "pre_boundary",
            "start_unknown",
        }:
            errors.append(
                f"invalid prospective status for {conversation.get('conversation_key')}"
            )
        if conversation.get("prospective_status") == "eligible" and (
            not conversation.get("start_time")
            or conversation.get("start_time_source") == "unavailable"
        ):
            errors.append(
                f"eligible conversation lacks authoritative start time in {batch}"
            )
        for index, turn in enumerate(turns):
            turn_location = (
                f"conversation {conversation.get('conversation_key')} turn {index} in {batch}"
            )
            if not isinstance(turn, dict):
                errors.append(f"conversation turn is not an object at {turn_location}")
                continue
            errors.extend(
                _validate_reply_visual_metadata(turn, location=turn_location)
            )
            turn_post_id = str(turn.get("post_id") or "")
            canonical_post = canonical_post_lookup.get(turn_post_id)
            if canonical_post is None:
                errors.append(
                    f"conversation turn has no canonical post at {turn_location}"
                )
            elif not _reply_visual_metadata_matches(turn, canonical_post):
                errors.append(
                    f"conversation turn visual metadata disagrees with canonical post at {turn_location}"
                )
    for candidate in candidates:
        required_candidate_fields = {
            "schema_version",
            "candidate_key",
            "conversation_key",
            "branch_key",
            "root_post_id",
            "branch_tip_post_id",
            "segment_start_post_id",
            "source_branch_tip_post_id",
            "source_branch_tip_post_ids",
            "principal_author_key",
            "start_time",
            "last_activity_time",
            "activity_status",
            "prospective_status",
            "same_author_user_turn_count",
            "same_author_continuation_depth",
            "account_turn_count_on_path",
            "substantive_turn_count_on_path",
            "review_reason_codes",
            "correction_cues",
            "path_turns",
            "handoff_context_refs",
            "sibling_context_refs",
            "reconstruction_confidence",
            "reply_visual_context_summaries",
            "warnings",
        }
        if not required_candidate_fields <= set(candidate):
            errors.append(f"review candidate schema is incomplete in {batch}")
            continue
        if candidate.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"review candidate schema version is invalid in {batch}")
        if candidate.get("prospective_status") != "eligible":
            errors.append(f"non-eligible review candidate in {batch}")
        path_turns = candidate.get("path_turns")
        if not isinstance(path_turns, list) or not path_turns:
            errors.append(f"review candidate path is empty in {batch}")
        elif not all(isinstance(turn, dict) for turn in path_turns):
            errors.append(f"review candidate path contains a non-object in {batch}")
        else:
            for index, turn in enumerate(path_turns):
                turn_location = (
                    f"review candidate {candidate.get('candidate_key')} path turn {index} in {batch}"
                )
                errors.extend(
                    _validate_reply_visual_metadata(turn, location=turn_location)
                )
                turn_post_id = str(turn.get("post_id") or "")
                canonical_post = canonical_post_lookup.get(turn_post_id)
                if canonical_post is None:
                    errors.append(
                        f"review candidate path turn has no canonical post at {turn_location}"
                    )
                elif not _reply_visual_metadata_matches(turn, canonical_post):
                    errors.append(
                        "review candidate path turn visual metadata disagrees "
                        f"with canonical post at {turn_location}"
                    )
            if candidate.get("branch_tip_post_id") != path_turns[-1].get("post_id"):
                errors.append(f"review candidate branch tip is inconsistent in {batch}")
            principal = candidate.get("principal_author_key")
            conversation_key = str(candidate.get("conversation_key") or "")
            branch_tip = str(candidate.get("branch_tip_post_id") or "")
            segment_start = str(candidate.get("segment_start_post_id") or "")
            expected_branch_key = stable_id(
                "branch",
                conversation_key,
                str(principal or ""),
                segment_start,
                branch_tip,
            )
            if candidate.get("branch_key") != expected_branch_key:
                errors.append(f"review candidate branch key is invalid in {batch}")
            if candidate.get("candidate_key") != stable_id(
                "candidate", expected_branch_key
            ):
                errors.append(f"review candidate key is invalid in {batch}")
            if path_turns[0].get("post_id") != segment_start:
                errors.append(f"review candidate segment start is inconsistent in {batch}")
            if any(
                child.get("parent_post_id") != parent.get("post_id")
                for parent, child in zip(path_turns, path_turns[1:])
            ):
                errors.append(f"review candidate path is not parent-linked in {batch}")
            if any(
                turn.get("author_role") == "user"
                and turn.get("author_key") != principal
                for turn in path_turns
            ):
                errors.append(
                    f"review candidate path contains a foreign user author in {batch}"
                )
            observed_principal_turns = sum(
                turn.get("author_role") == "user"
                and turn.get("author_key") == principal
                for turn in path_turns
            )
            if candidate.get("same_author_user_turn_count") != observed_principal_turns:
                errors.append(f"review candidate author turn count is invalid in {batch}")
            observed_account_turns = sum(
                turn.get("author_role") == "account"
                for turn in path_turns
            )
            if candidate.get("account_turn_count_on_path") != observed_account_turns:
                errors.append(f"review candidate account turn count is invalid in {batch}")
            expected_depth = _same_author_continuation_depth(path_turns, str(principal or ""))
            if candidate.get("same_author_continuation_depth") != expected_depth:
                errors.append(f"review candidate continuation depth is invalid in {batch}")
            observed_substantive_turns = sum(
                turn.get("substantive") is True for turn in path_turns
            )
            if (
                candidate.get("substantive_turn_count_on_path")
                != observed_substantive_turns
            ):
                errors.append(f"review candidate substantive turn count is invalid in {batch}")
        visual_summaries = candidate.get("reply_visual_context_summaries")
        if not isinstance(visual_summaries, list):
            errors.append(f"review candidate visual summaries are not a list in {batch}")
        else:
            summary_post_ids: list[str] = []
            for index, summary in enumerate(visual_summaries):
                summary_location = (
                    f"review candidate {candidate.get('candidate_key')} visual summary {index} in {batch}"
                )
                errors.extend(
                    _validate_reply_visual_context_summary_shape(
                        summary,
                        location=summary_location,
                        include_post_id=True,
                    )
                )
                if isinstance(summary, dict) and isinstance(
                    summary.get("post_id"), str
                ):
                    summary_post_ids.append(summary["post_id"])
            if len(summary_post_ids) != len(set(summary_post_ids)):
                errors.append(f"review candidate visual summary post IDs are duplicated in {batch}")
            if (
                isinstance(path_turns, list)
                and all(isinstance(turn, dict) for turn in path_turns)
                and visual_summaries != _reply_visual_context_summaries(path_turns)
            ):
                errors.append(f"review candidate visual summaries are inconsistent in {batch}")
        sibling_refs = candidate.get("sibling_context_refs")
        if (
            not isinstance(sibling_refs, list)
            or len(sibling_refs) > MAX_SIBLING_CONTEXT_REFS
            or not all(isinstance(row, dict) for row in sibling_refs)
        ):
            errors.append(f"review candidate sibling context is invalid in {batch}")
        handoff_refs = candidate.get("handoff_context_refs")
        if (
            not isinstance(handoff_refs, list)
            or len(handoff_refs) > MAX_HANDOFF_CONTEXT_REFS
            or not all(isinstance(row, dict) for row in handoff_refs)
        ):
            errors.append(f"review candidate hand-off context is invalid in {batch}")
            handoff_refs = []
        source_tips = candidate.get("source_branch_tip_post_ids")
        if (
            not isinstance(source_tips, list)
            or not source_tips
            or source_tips != sorted(set(str(value) for value in source_tips))
            or candidate.get("source_branch_tip_post_id") != source_tips[0]
        ):
            errors.append(f"review candidate source branch tips are invalid in {batch}")
        if isinstance(path_turns, list) and path_turns:
            first_parent = str(path_turns[0].get("parent_post_id") or "")
            if (
                first_parent
                and path_turns[0].get("post_id") != candidate.get("root_post_id")
                and first_parent
                not in {str(row.get("post_id") or "") for row in handoff_refs}
            ):
                errors.append(
                    f"review candidate external first parent lacks hand-off context in {batch}"
                )
        reasons = candidate.get("review_reason_codes")
        if not isinstance(reasons, list) or reasons != [
            reason for reason in REVIEW_REASON_ORDER if reason in set(reasons or [])
        ]:
            errors.append(f"review candidate reason ordering is invalid in {batch}")
        if isinstance(reasons, list) and (
            bool(handoff_refs)
            != ("external_author_handoff_context" in reasons)
        ):
            errors.append(f"review candidate hand-off reason is invalid in {batch}")
        prohibited = {
            "defect",
            "bad_reply",
            "proposition_substitution",
            "repair_required",
            "false_concession",
        }
        if prohibited & set(candidate.get("review_reason_codes") or []):
            errors.append(f"prohibited adjudicative review reason in {batch}")
    ignored_kinds = source_manifest.get("ignored_structured_event_kinds")
    if (
        not isinstance(ignored_kinds, list)
        or len(ignored_kinds) > MAX_IGNORED_STRUCTURED_EVENT_KINDS
    ):
        errors.append(f"ignored structured-event histogram is invalid in {batch}")
        ignored_kinds = []
    else:
        histogram_order: list[tuple[int, str]] = []
        for row in ignored_kinds:
            if not isinstance(row, dict) or set(row) != {
                "event_kind",
                "count",
                "target_like_count",
            }:
                errors.append(f"ignored structured-event histogram row is invalid in {batch}")
                continue
            if (
                not isinstance(row.get("event_kind"), str)
                or not row.get("event_kind")
                or type(row.get("count")) is not int
                or int(row["count"]) < 0
                or type(row.get("target_like_count")) is not int
                or int(row["target_like_count"]) < 0
                or int(row["target_like_count"]) > int(row["count"])
            ):
                errors.append(f"ignored structured-event histogram values are invalid in {batch}")
                continue
            histogram_order.append((-int(row["count"]), str(row["event_kind"])))
        if histogram_order != sorted(histogram_order):
            errors.append(f"ignored structured-event histogram ordering is invalid in {batch}")
    for field in (
        "ignored_structured_event_other_count",
        "ignored_target_like_event_other_count",
    ):
        if type(source_manifest.get(field)) is not int or int(source_manifest[field]) < 0:
            errors.append(f"{field} is invalid in {batch}")
    structured_statistics = source_manifest.get("structured_event_statistics")
    if not isinstance(structured_statistics, dict):
        errors.append(f"structured-event statistics are invalid in {batch}")
    elif (
        type(structured_statistics.get("ignored_structured_event_count")) is int
        and type(structured_statistics.get("ignored_target_like_event_count")) is int
        and type(source_manifest.get("ignored_structured_event_other_count")) is int
        and type(source_manifest.get("ignored_target_like_event_other_count")) is int
    ):
        retained_ignored = sum(
            int(row.get("count") or 0)
            for row in ignored_kinds
            if isinstance(row, dict) and type(row.get("count")) is int
        )
        retained_target_like = sum(
            int(row.get("target_like_count") or 0)
            for row in ignored_kinds
            if isinstance(row, dict)
            and type(row.get("target_like_count")) is int
        )
        if (
            retained_ignored
            + int(source_manifest["ignored_structured_event_other_count"])
            != structured_statistics["ignored_structured_event_count"]
        ):
            errors.append(f"ignored structured-event remainder is inaccurate in {batch}")
        if (
            retained_target_like
            + int(source_manifest["ignored_target_like_event_other_count"])
            != structured_statistics["ignored_target_like_event_count"]
        ):
            errors.append(f"ignored target-like event remainder is inaccurate in {batch}")
    lag = status_value.get("source_lag_seconds")
    if type(lag) is not int or lag < 0:
        errors.append(f"source lag is invalid in {batch}")
    else:
        status_warnings = status_value.get("warnings")
        if not isinstance(status_warnings, list):
            errors.append(f"batch status warnings are invalid in {batch}")
        elif (
            lag > SOURCE_STALE_WARNING_SECONDS
            and "retained_source_stale_relative_to_scan_cutoff"
            not in status_warnings
        ):
            errors.append(f"stale source lag warning is missing in {batch}")
    observed_counts = {
        "canonical_post_count": len(posts),
        "open_conversation_count": sum(
            row.get("activity_status") == "open" for row in conversations
        ),
        "prospective_eligible_conversation_count": sum(
            row.get("prospective_status") == "eligible" for row in conversations
        ),
        "quiescent_conversation_count": sum(
            row.get("activity_status") == "quiescent" for row in conversations
        ),
        "reconstructed_conversation_count": len(conversations),
        "review_candidate_count": len(candidates),
        "source_file_count": len(source_files) if isinstance(source_files, list) else -1,
    }
    if manifest.get("counts") != observed_counts:
        errors.append(f"batch manifest counts do not match outputs in {batch}")
    for key, value in observed_counts.items():
        if status_value.get(key) != value:
            errors.append(f"batch status count {key} does not match outputs in {batch}")
    if require_immutable and isinstance(manifest.get("canonical_snapshot_sha256"), str):
        suffix = str(manifest["canonical_snapshot_sha256"])[:12]
        if not batch.name.endswith(f"-{suffix}"):
            errors.append(f"batch name hash prefix does not match manifest in {batch}")
    return errors


def _publish_batch(
    root: Path,
    *,
    batch_id: str,
    files: Mapping[str, bytes],
    state_value: Mapping[str, Any],
    finalise_state: Callable[[], Mapping[str, Any]] | None = None,
) -> None:
    batches_dir = root / "batches"
    temporary = Path(
        tempfile.mkdtemp(prefix=".tmp-", dir=batches_dir)
    )
    os.chmod(temporary, 0o700, follow_symlinks=False)
    final = batches_dir / batch_id
    if final.exists() or final.is_symlink():
        shutil.rmtree(temporary)
        raise ExtractorError(f"refusing to overwrite existing batch: {batch_id}")
    previous_target = _current_link_target(root, required=False)
    new_target = f"batches/{batch_id}"
    moved = False
    committed = False
    try:
        for name in BATCH_FILES:
            _write_file(temporary / name, files[name])
        _fsync_directory(temporary)
        problems = _validate_batch_directory(temporary, require_immutable=False)
        if problems:
            raise ExtractorError("temporary batch validation failed: " + "; ".join(problems))
        os.replace(temporary, final)
        moved = True
        _fsync_directory(batches_dir)
        _make_tree_read_only(final)
        _set_current(root, new_target)
        try:
            final_state = dict(state_value)
            if finalise_state is not None:
                final_state.update(finalise_state())
            _atomic_write_state(root, final_state)
        except Exception:
            _restore_current(root, previous_target, new_target)
            _remove_new_tree(final)
            moved = False
            raise
        committed = True
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        if moved and not committed:
            _restore_current(root, previous_target, new_target)
            _remove_new_tree(final)


@contextmanager
def _private_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def run_scan(
    *,
    project_dir: Path,
    output_root: Path,
    prospective_start: str,
    until: str | None = None,
    quiescence_hours: float = 48.0,
    scan_start: datetime | None = None,
) -> dict[str, Any]:
    """Run one locked scan and return its concise journal result."""
    if not isinstance(quiescence_hours, (int, float)) or isinstance(
        quiescence_hours, bool
    ):
        raise ExtractorError("--quiescence-hours must be a positive number")
    if quiescence_hours <= 0:
        raise ExtractorError("--quiescence-hours must be greater than zero")
    boundary_value = parse_aware_timestamp(
        prospective_start, option="--prospective-start"
    )
    boundary_text = str(format_utc(boundary_value))
    started = (scan_start or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )
    cutoff = (
        parse_aware_timestamp(until, option="--until") if until is not None else started
    )
    if cutoff < boundary_value:
        raise ExtractorError("--until must be at or after --prospective-start")
    project, root = validate_root_relationship(project_dir, output_root)
    extractor_code_provenance = _extractor_code_provenance()

    with _private_umask():
        ensure_private_layout(root)
        with extractor_lock(
            root, exclusive=True, nonblocking=True, create=True
        ) as acquired:
            if not acquired:
                return {
                    "message": "another prospective conversation scan holds the lock",
                    "status": "lock_held",
                }
            state_value = read_extractor_state(root)
            if state_value is not None:
                stored_boundary = str(state_value["prospective_boundary"])
                if boundary_text != stored_boundary:
                    raise ExtractorError(
                        "prospective boundary mismatch: supplied "
                        f"{boundary_text}, extractor state requires {stored_boundary}"
                    )
                previous_cutoff = parse_optional_timestamp(
                    state_value.get("last_scan_cutoff")
                )
                if previous_cutoff is not None and cutoff < previous_cutoff:
                    raise ExtractorError(
                        "--until cannot move backwards in an initialised extractor root"
                    )
            pseudonym_key = load_or_create_pseudonym_key(root)
            prior_posts = _load_prior_posts(root, state_value)
            inventory = collect_source_inventory(project)
            if not inventory.files:
                raise ExtractorError("no eligible retained production log files were found")
            parsed = parse_incremental_sources(
                inventory, state_value, cutoff=cutoff
            )
            parser_statistics: dict[str, Any] = {}
            posts = normalise_canonical_posts(
                parsed.records,
                prior_posts,
                pseudonym_key,
                parser_statistics=parser_statistics,
            )
            conversations, candidates = build_conversations(
                posts,
                boundary=boundary_value,
                cutoff=cutoff,
                quiescence_hours=float(quiescence_hours),
            )
            coverage = bool(
                parsed.earliest_source_timestamp
                and parsed.latest_source_timestamp
                and str(parsed.earliest_source_timestamp) <= boundary_text
                <= str(parsed.latest_source_timestamp)
            )
            latest_source_value = parse_optional_timestamp(
                parsed.latest_source_timestamp
            )
            source_lag_seconds = int(
                max(
                    0.0,
                    (cutoff - latest_source_value).total_seconds()
                    if latest_source_value is not None
                    else 0.0,
                )
            )
            warnings: list[str] = []
            warnings.extend(
                f"{warning.kind}:{warning.basename}" for warning in inventory.warnings
            )
            warnings.extend(
                f"incomplete_trailing_line_ignored:{source.basename}"
                for source in inventory.files
                if source.incomplete_trailing_line_ignored
            )
            if parsed.warnings:
                warnings.append(f"log_parse_warning_count:{len(parsed.warnings)}")
            if not coverage:
                warnings.append("retained_source_coverage_does_not_span_prospective_boundary")
            if source_lag_seconds > SOURCE_STALE_WARNING_SECONDS:
                warnings.append("retained_source_stale_relative_to_scan_cutoff")
            warnings = sorted(set(warnings))

            (
                ignored_event_kinds,
                ignored_event_other_count,
                ignored_target_like_other_count,
            ) = ignored_structured_event_histogram(parser_statistics)
            scalar_parser_statistics = {
                key: value
                for key, value in sorted(parser_statistics.items())
                if type(value) is int
            }

            canonical_data = jsonl_bytes(posts)
            conversations_data = jsonl_bytes(conversations)
            candidates_data = jsonl_bytes(candidates)
            canonical_hash = sha256_bytes(canonical_data)
            conversations_hash = sha256_bytes(conversations_data)
            candidates_hash = sha256_bytes(candidates_data)
            snapshot_hash = _snapshot_hash(
                canonical_hash, conversations_hash, candidates_hash
            )
            counts = _count_snapshot(
                posts, conversations, candidates, len(inventory.files)
            )
            cutoff_text = str(format_utc(cutoff))
            started_text = str(format_utc(started))
            completed = datetime.now(timezone.utc).replace(microsecond=0)
            completed_text = str(format_utc(completed))
            previous_batch = (
                str((state_value or {}).get("last_successful_batch") or "") or None
            )
            batch_id = f"{compact_utc(cutoff)}-{snapshot_hash[:12]}"

            unchanged = bool(
                state_value is not None
                and snapshot_hash == state_value.get("current_snapshot_hash")
            )

            def persist_storage_failure(
                exc: ExtractorError,
                *,
                warning: str,
                projected_bytes: int,
                retained: RetentionResult | None = None,
            ) -> None:
                if state_value is None:
                    return
                usage = shutil.disk_usage(root)
                failed_state = copy.deepcopy(dict(state_value))
                failed_warnings = set(failed_state.get("warnings") or [])
                failed_warnings.add(warning)
                if retained is not None:
                    failed_state.update(retained.as_state_fields())
                failed_state.update(
                    {
                        "automatic_batch_budget_bytes": MAX_AUTOMATIC_BATCH_BYTES,
                        "filesystem_free_bytes": int(usage.free),
                        "filesystem_total_bytes": int(usage.total),
                        "filesystem_used_bytes": int(usage.used),
                        "last_retention_error": str(exc),
                        "last_scan_completion": str(
                            format_utc(
                                datetime.now(timezone.utc).replace(microsecond=0)
                            )
                        ),
                        "last_scan_start": started_text,
                        "minimum_free_bytes": MIN_FILESYSTEM_FREE_BYTES,
                        "projected_batch_bytes": projected_bytes,
                        "required_free_bytes": (
                            projected_bytes + MIN_FILESYSTEM_FREE_BYTES
                        ),
                        "warnings": sorted(failed_warnings),
                    }
                )
                _atomic_write_state(root, failed_state)

            def next_state_value(
                retained: RetentionResult,
                *,
                projected_bytes: int,
                usage: Any,
                successful_batch: str,
            ) -> dict[str, Any]:
                return {
                    "automatic_batch_budget_bytes": MAX_AUTOMATIC_BATCH_BYTES,
                    "counts": counts,
                    "current_snapshot_hash": snapshot_hash,
                    "extractor_version": EXTRACTOR_VERSION,
                    "filesystem_free_bytes": int(usage.free),
                    "filesystem_total_bytes": int(usage.total),
                    "filesystem_used_bytes": int(usage.used),
                    "last_retention_error": None,
                    "last_scan_completion": completed_text,
                    "last_scan_cutoff": cutoff_text,
                    "last_scan_start": started_text,
                    "last_successful_batch": successful_batch,
                    "last_successful_completion": completed_text,
                    "latest_source_timestamp": parsed.latest_source_timestamp,
                    "minimum_free_bytes": MIN_FILESYSTEM_FREE_BYTES,
                    "parser_version": PARSER_VERSION,
                    "projected_batch_bytes": projected_bytes,
                    "prospective_boundary": boundary_text,
                    "quiescence_hours": float(quiescence_hours),
                    "required_free_bytes": (
                        projected_bytes + MIN_FILESYSTEM_FREE_BYTES
                        if projected_bytes
                        else MIN_FILESYSTEM_FREE_BYTES
                    ),
                    "schema_version": SCHEMA_VERSION,
                    "source_lag_seconds": source_lag_seconds,
                    "source_file_cache": parsed.source_cache,
                    "source_file_count": len(inventory.files),
                    "warnings": warnings,
                    **retained.as_state_fields(),
                }

            if unchanged:
                try:
                    retention_before = apply_batch_retention(
                        root,
                        now=started,
                        expected_boundary=boundary_text,
                        projected_batch_bytes=0,
                    )
                except ExtractorError as exc:
                    persist_storage_failure(
                        exc,
                        warning="automatic_batch_retention_failed",
                        projected_bytes=0,
                    )
                    raise
                usage = shutil.disk_usage(root)
                next_state = next_state_value(
                    retention_before,
                    projected_bytes=0,
                    usage=usage,
                    successful_batch=str(state_value.get("last_successful_batch") or ""),
                )
                _atomic_write_state(root, next_state)
                return {
                    "batch": state_value.get("last_successful_batch"),
                    "counts": counts,
                    "message": "prospective conversation corpus unchanged",
                    "snapshot_hash": snapshot_hash,
                    "source_lag_seconds": source_lag_seconds,
                    "status": "no_change",
                }

            source_manifest_value = {
                "extractor_version": EXTRACTOR_VERSION,
                "inventory_retry_count": inventory.retry_count,
                "ignored_structured_event_kinds": ignored_event_kinds,
                "ignored_structured_event_other_count": ignored_event_other_count,
                "ignored_target_like_event_other_count": ignored_target_like_other_count,
                "ordering": "mrsMThatcher.log.100 through .1, then mrsMThatcher.log",
                "parser_version": PARSER_VERSION,
                "parse_warnings": list(parsed.warnings),
                "parsed_source_hash_count": parsed.parsed_source_hash_count,
                "prospective_boundary": boundary_text,
                "reused_source_hash_count": parsed.reused_source_hash_count,
                "schema_version": SCHEMA_VERSION,
                "source_files": [source.manifest_row() for source in inventory.files],
                "source_warnings": [warning.as_json() for warning in inventory.warnings],
                "structured_event_statistics": scalar_parser_statistics,
            }
            source_manifest_data = canonical_json_bytes(source_manifest_value)
            status_value = {
                **counts,
                "current_snapshot_hash": snapshot_hash,
                "extractor_version": EXTRACTOR_VERSION,
                "latest_source_timestamp": parsed.latest_source_timestamp,
                "parser_version": PARSER_VERSION,
                "prospective_boundary": boundary_text,
                "scan_cutoff": cutoff_text,
                "schema_version": SCHEMA_VERSION,
                "source_lag_seconds": source_lag_seconds,
                "warnings": warnings,
            }
            status_data = canonical_json_bytes(status_value)
            report_data = _build_extraction_report(
                boundary=boundary_text,
                cutoff=cutoff_text,
                counts=counts,
                coverage=coverage,
                source_lag_seconds=source_lag_seconds,
                warnings=warnings,
            )
            non_manifest_files = {
                "canonical-posts.jsonl": canonical_data,
                "conversations.jsonl": conversations_data,
                "extraction-report.md": report_data,
                "review-candidates.jsonl": candidates_data,
                "source-manifest.json": source_manifest_data,
                "status.json": status_data,
            }
            output_hashes = {
                name: sha256_bytes(non_manifest_files[name])
                for name in BATCH_HASHED_FILES
            }
            if _extractor_code_provenance() != extractor_code_provenance:
                raise ExtractorError(
                    "extractor code provenance changed during the scan"
                )
            manifest_value = {
                "canonical_snapshot_sha256": snapshot_hash,
                "counts": counts,
                "creation_timestamp": started_text,
                **extractor_code_provenance,
                "extractor_version": EXTRACTOR_VERSION,
                "output_file_hashes": output_hashes,
                "parser_version": PARSER_VERSION,
                "previous_batch_id": previous_batch,
                "prospective_boundary": boundary_text,
                "quiescence_hours": float(quiescence_hours),
                "repository_commit_sha": extractor_code_provenance[
                    "extractor_repository_commit_sha"
                ],
                "scan_cutoff": cutoff_text,
                "schema_version": SCHEMA_VERSION,
                "source_lag_seconds": source_lag_seconds,
                "source_coverage_reaches_prospective_boundary": coverage,
                "source_manifest_sha256": output_hashes["source-manifest.json"],
                "warnings": warnings,
            }
            all_files = {
                **non_manifest_files,
                "manifest.json": canonical_json_bytes(manifest_value),
            }
            projected_batch_bytes = sum(len(data) for data in all_files.values())
            try:
                retention_before = apply_batch_retention(
                    root,
                    now=started,
                    expected_boundary=boundary_text,
                    projected_batch_bytes=projected_batch_bytes,
                )
            except ExtractorError as exc:
                persist_storage_failure(
                    exc,
                    warning="automatic_batch_retention_failed",
                    projected_bytes=projected_batch_bytes,
                )
                raise

            usage = shutil.disk_usage(root)
            filesystem_free_bytes = int(usage.free)
            required_free_bytes = projected_batch_bytes + MIN_FILESYSTEM_FREE_BYTES
            if filesystem_free_bytes < required_free_bytes:
                exc = ExtractorError(
                    "insufficient free space for prospective snapshot: "
                    f"filesystem_total_bytes={int(usage.total)} "
                    f"filesystem_used_bytes={int(usage.used)} "
                    f"filesystem_free_bytes={filesystem_free_bytes} "
                    f"projected_batch_bytes={projected_batch_bytes} "
                    f"minimum_free_bytes={MIN_FILESYSTEM_FREE_BYTES} "
                    f"required_free_bytes={required_free_bytes} "
                    f"automatic_batch_budget_bytes={MAX_AUTOMATIC_BATCH_BYTES} "
                    f"retained_automatic_bytes={retention_before.retained_automatic_bytes} "
                    f"protected_automatic_bytes={retention_before.protected_automatic_bytes}"
                )
                persist_storage_failure(
                    exc,
                    warning="disk_space_preflight_failed",
                    projected_bytes=projected_batch_bytes,
                    retained=retention_before,
                )
                raise exc

            next_state = next_state_value(
                retention_before,
                projected_bytes=projected_batch_bytes,
                usage=usage,
                successful_batch=batch_id,
            )

            def finalise_published_state() -> Mapping[str, Any]:
                measured = measure_retained_storage(
                    root, expected_boundary=boundary_text
                )
                final_usage = shutil.disk_usage(root)
                fields = measured.as_state_fields()
                fields.update(
                    {
                        "filesystem_free_bytes": int(final_usage.free),
                        "filesystem_total_bytes": int(final_usage.total),
                        "filesystem_used_bytes": int(final_usage.used),
                        "pruned_batch_ids": list(retention_before.pruned_batch_ids),
                        "retention_full_validation_batch_count": (
                            retention_before.retention_full_validation_batch_count
                        ),
                        "retention_lightweight_protected_batch_count": (
                            retention_before.retention_lightweight_protected_batch_count
                        ),
                    }
                )
                return fields

            _publish_batch(
                root,
                batch_id=batch_id,
                files=all_files,
                state_value=next_state,
                finalise_state=finalise_published_state,
            )
            return {
                "batch": batch_id,
                "counts": counts,
                "message": "published new prospective conversation snapshot",
                "snapshot_hash": snapshot_hash,
                "source_lag_seconds": source_lag_seconds,
                "status": "published",
            }


def uninitialised_status() -> dict[str, Any]:
    """Return the stable status schema for an uninitialised output root."""
    return {
        "automatic_batch_budget_bytes": MAX_AUTOMATIC_BATCH_BYTES,
        "canonical_post_count": 0,
        "current_snapshot_hash": None,
        "extractor_version": EXTRACTOR_VERSION,
        "filesystem_free_bytes": None,
        "filesystem_total_bytes": None,
        "filesystem_used_bytes": None,
        "initialised": False,
        "last_retention_error": None,
        "last_scan_completion": None,
        "last_scan_start": None,
        "last_successful_batch": None,
        "latest_source_timestamp": None,
        "minimum_free_bytes": MIN_FILESYSTEM_FREE_BYTES,
        "open_conversation_count": 0,
        "parser_version": PARSER_VERSION,
        "projected_batch_bytes": 0,
        "pruned_batch_ids": [],
        "protected_automatic_bytes": 0,
        "prospective_boundary": None,
        "prospective_eligible_conversation_count": 0,
        "quiescent_conversation_count": 0,
        "reconstructed_conversation_count": 0,
        "required_free_bytes": 0,
        "retained_automatic_bytes": 0,
        "retained_batch_count": 0,
        "retention_full_validation_batch_count": 0,
        "retention_lightweight_protected_batch_count": 0,
        "review_candidate_count": 0,
        "review_pack_bytes": 0,
        "schema_version": SCHEMA_VERSION,
        "source_file_count": 0,
        "source_lag_seconds": None,
        "total_extractor_bytes": 0,
        "warnings": [],
    }


def get_status(output_root: Path) -> dict[str, Any]:
    """Return stable read-only status, including for an uninitialised root."""
    root = _absolute_without_following(output_root)
    state_path = root / "state" / "extractor-state.json"
    if not state_path.exists() and not state_path.is_symlink():
        return uninitialised_status()
    _require_real_directory(root, label="output root")
    _require_real_directory(root / "state", label="state directory")
    with extractor_lock(
        root, exclusive=False, nonblocking=False, create=False
    ) as acquired:
        if not acquired:
            raise ExtractorError("could not acquire shared extractor lock")
        state_value = read_extractor_state(root, missing_ok=False)
        assert state_value is not None
        counts = state_value.get("counts")
        if not isinstance(counts, dict):
            raise ExtractorError("extractor state counts must be an object")
        boundary = str(state_value.get("prospective_boundary") or "")
        storage = measure_retained_storage(
            root, expected_boundary=boundary or None
        )
        usage = shutil.disk_usage(root)
        telemetry_fields = {
            "protected_automatic_bytes": storage.protected_automatic_bytes,
            "retained_automatic_bytes": storage.retained_automatic_bytes,
            "retained_batch_count": storage.retained_batch_count,
            "review_pack_bytes": storage.review_pack_bytes,
            "total_extractor_bytes": storage.total_extractor_bytes,
        }
        status_warnings = set(state_value.get("warnings") or [])
        status_warnings.update(storage.storage_warnings)
        if any(
            state_value.get(field) != observed
            for field, observed in telemetry_fields.items()
        ):
            status_warnings.add("stored_storage_telemetry_stale")
        result = uninitialised_status()
        result.update(
            {
                "automatic_batch_budget_bytes": MAX_AUTOMATIC_BATCH_BYTES,
                "canonical_post_count": int(
                    counts.get("canonical_post_count") or 0
                ),
                "current_snapshot_hash": state_value.get("current_snapshot_hash"),
                "extractor_version": state_value.get("extractor_version"),
                "filesystem_free_bytes": int(usage.free),
                "filesystem_total_bytes": int(usage.total),
                "filesystem_used_bytes": int(usage.used),
                "initialised": True,
                "last_retention_error": state_value.get("last_retention_error"),
                "last_scan_completion": state_value.get("last_scan_completion"),
                "last_scan_start": state_value.get("last_scan_start"),
                "last_successful_batch": state_value.get("last_successful_batch"),
                "latest_source_timestamp": state_value.get("latest_source_timestamp"),
                "minimum_free_bytes": MIN_FILESYSTEM_FREE_BYTES,
                "open_conversation_count": int(
                    counts.get("open_conversation_count") or 0
                ),
                "parser_version": state_value.get("parser_version"),
                "projected_batch_bytes": int(
                    state_value.get("projected_batch_bytes") or 0
                ),
                "pruned_batch_ids": list(
                    state_value.get("pruned_batch_ids") or []
                ),
                "prospective_boundary": state_value.get("prospective_boundary"),
                "prospective_eligible_conversation_count": int(
                    counts.get("prospective_eligible_conversation_count") or 0
                ),
                "quiescent_conversation_count": int(
                    counts.get("quiescent_conversation_count") or 0
                ),
                "reconstructed_conversation_count": int(
                    counts.get("reconstructed_conversation_count") or 0
                ),
                "retention_full_validation_batch_count": int(
                    state_value.get("retention_full_validation_batch_count") or 0
                ),
                "retention_lightweight_protected_batch_count": int(
                    state_value.get(
                        "retention_lightweight_protected_batch_count"
                    )
                    or 0
                ),
                "review_candidate_count": int(
                    counts.get("review_candidate_count") or 0
                ),
                "required_free_bytes": int(
                    state_value.get("required_free_bytes") or 0
                ),
                "source_file_count": int(
                    state_value.get("source_file_count") or 0
                ),
                "source_lag_seconds": (
                    int(state_value["source_lag_seconds"])
                    if type(state_value.get("source_lag_seconds")) is int
                    else None
                ),
                "warnings": sorted(status_warnings),
                **telemetry_fields,
            }
        )
        return result


def _validate_pseudonym_key_read_only(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        descriptor = _open_regular_nofollow(path, os.O_RDONLY)
    except OSError as exc:
        return [f"cannot open pseudonym key: {exc}"]
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            errors.append("pseudonym key is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            errors.append("pseudonym key permissions are not 0600")
        data = os.read(descriptor, 33)
        if len(data) != 32:
            errors.append("pseudonym key does not contain exactly 32 bytes")
    finally:
        os.close(descriptor)
    return errors


def _validate_pack_directory(
    pack: Path,
    *,
    require_immutable: bool,
    expected_boundary: str | None,
) -> list[str]:
    errors: list[str] = []
    try:
        info = pack.lstat()
    except OSError as exc:
        return [f"cannot inspect review pack {pack}: {exc}"]
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return [f"review pack is not a non-symlink directory: {pack}"]
    if require_immutable and stat.S_IMODE(info.st_mode) != 0o500:
        errors.append(f"review pack directory mode is not 0500: {pack}")
    names = sorted(path.name for path in pack.iterdir())
    if names != sorted(PACK_FILES):
        errors.append(f"review pack file set is incorrect in {pack}")
        return errors
    for name in PACK_FILES:
        path = pack / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            errors.append(f"review pack output is not a regular file: {path}")
        if require_immutable and stat.S_IMODE(info.st_mode) != 0o400:
            errors.append(f"review pack output mode is not 0400: {path}")
    if errors:
        return errors
    try:
        manifest = _strict_read_json(pack / "manifest.json")
        conversations = _load_jsonl(pack / "conversations.jsonl")
        candidates = _load_jsonl(pack / "review-candidates.jsonl")
    except ExtractorError as exc:
        return [str(exc)]
    if not isinstance(manifest, dict):
        return [f"review pack manifest is not an object: {pack}"]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported review pack schema: {pack}")
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        errors.append(f"review pack extractor version mismatch: {pack}")
    if manifest.get("parser_version") != PARSER_VERSION:
        errors.append(f"review pack parser version mismatch: {pack}")
    try:
        parse_aware_timestamp(
            str(manifest.get("creation_timestamp") or ""),
            option="review pack creation timestamp",
        )
        parse_aware_timestamp(
            str(manifest.get("source_batch_creation_timestamp") or ""),
            option="review pack source batch creation timestamp",
        )
    except ExtractorError as exc:
        errors.append(str(exc))
    if (pack / "manifest.json").read_bytes() != canonical_json_bytes(manifest):
        errors.append(f"review pack manifest is not in canonical JSON form: {pack}")
    if expected_boundary and manifest.get("prospective_boundary") != expected_boundary:
        errors.append(f"review pack boundary mismatch: {pack}")
    hashes = manifest.get("output_file_hashes")
    if not isinstance(hashes, dict) or set(hashes) != set(PACK_HASHED_FILES):
        errors.append(f"review pack output hash set is incorrect: {pack}")
    else:
        for name in PACK_HASHED_FILES:
            if hashes.get(name) != sha256_file(pack / name):
                errors.append(f"review pack output hash mismatch: {pack.name}/{name}")
        expected_pack_hash = sha256_bytes(
            canonical_json_bytes(
                {
                    "conversations_sha256": hashes.get("conversations.jsonl"),
                    "extractor_version": EXTRACTOR_VERSION,
                    "parser_version": PARSER_VERSION,
                    "review_candidates_sha256": hashes.get(
                        "review-candidates.jsonl"
                    ),
                    "review_pack_markdown_sha256": hashes.get("review-pack.md"),
                    "schema_version": SCHEMA_VERSION,
                },
                newline=False,
            )
        )
        if manifest.get("pack_content_sha256") != expected_pack_hash:
            errors.append(f"review pack content hash mismatch: {pack}")
    for name, rows in (
        ("conversations.jsonl", conversations),
        ("review-candidates.jsonl", candidates),
    ):
        for index, row in enumerate(rows, start=1):
            for problem in _walk_forbidden_keys(row):
                errors.append(f"{pack.name}/{name}:{index}: {problem}")
    order = [
        (str(row.get("start_time") or ""), str(row.get("conversation_key") or ""))
        for row in conversations
    ]
    if order != sorted(order):
        errors.append(f"review pack conversation ordering is invalid: {pack}")
    return errors


def _validate_root_unlocked(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for path, mode, label in (
        (root, 0o700, "output root"),
        (root / "state", 0o700, "state directory"),
        (root / "batches", 0o700, "batches directory"),
        (root / "review-packs", 0o700, "review-packs directory"),
    ):
        try:
            info = path.lstat()
        except OSError as exc:
            errors.append(f"cannot inspect {label}: {exc}")
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            errors.append(f"{label} is not a non-symlink directory")
        elif stat.S_IMODE(info.st_mode) != mode:
            errors.append(f"{label} permissions are not {mode:04o}")
    if errors:
        return {
            "batch_count": 0,
            "errors": errors,
            "review_pack_count": 0,
            "schema_version": SCHEMA_VERSION,
            "valid": False,
            "warnings": warnings,
        }
    try:
        state_value = read_extractor_state(root, missing_ok=False)
    except ExtractorError as exc:
        errors.append(str(exc))
        state_value = None
    state_path = root / "state" / "extractor-state.json"
    try:
        state_info = state_path.lstat()
        if stat.S_ISLNK(state_info.st_mode) or not stat.S_ISREG(state_info.st_mode):
            errors.append("extractor state is not a regular file")
        elif stat.S_IMODE(state_info.st_mode) != 0o600:
            errors.append("extractor state permissions are not 0600")
        elif state_value is not None and state_path.read_bytes() != canonical_json_bytes(
            state_value
        ):
            errors.append("extractor state is not in canonical JSON form")
    except OSError as exc:
        errors.append(f"cannot inspect extractor state: {exc}")
    errors.extend(
        _validate_pseudonym_key_read_only(root / "state" / "pseudonym-key")
    )
    lock_path = root / "state" / "extractor.lock"
    try:
        lock_info = lock_path.lstat()
        if stat.S_ISLNK(lock_info.st_mode) or not stat.S_ISREG(lock_info.st_mode):
            errors.append("extractor lock is not a regular file")
        elif stat.S_IMODE(lock_info.st_mode) != 0o600:
            errors.append("extractor lock permissions are not 0600")
    except OSError as exc:
        errors.append(f"cannot inspect extractor lock: {exc}")

    boundary = str((state_value or {}).get("prospective_boundary") or "") or None
    batch_dirs: list[Path] = []
    for path in sorted((root / "batches").iterdir(), key=lambda item: item.name):
        if path.name.startswith(".tmp-"):
            errors.append(f"abandoned temporary batch exists: {path.name}")
        else:
            batch_dirs.append(path)
            errors.extend(
                _validate_batch_directory(
                    path,
                    require_immutable=True,
                    expected_boundary=boundary,
                )
            )
    pack_dirs: list[Path] = []
    for path in sorted((root / "review-packs").iterdir(), key=lambda item: item.name):
        if path.name.startswith(".tmp-"):
            errors.append(f"abandoned temporary review pack exists: {path.name}")
        else:
            pack_dirs.append(path)
            errors.extend(
                _validate_pack_directory(
                    path,
                    require_immutable=True,
                    expected_boundary=boundary,
                )
            )
    try:
        current_target = _current_link_target(root, required=True)
    except ExtractorError as exc:
        errors.append(str(exc))
        current_target = None
    if state_value is not None and current_target is not None:
        expected_batch = str(state_value.get("last_successful_batch") or "")
        if current_target != f"batches/{expected_batch}":
            errors.append("current target disagrees with last_successful_batch")
        current_manifest_path = root / current_target / "manifest.json"
        try:
            current_manifest = _strict_read_json(current_manifest_path)
        except ExtractorError as exc:
            errors.append(str(exc))
        else:
            if not isinstance(current_manifest, dict):
                errors.append("current manifest is not an object")
            elif current_manifest.get("canonical_snapshot_sha256") != state_value.get(
                "current_snapshot_hash"
            ):
                errors.append("current snapshot hash disagrees with extractor state")
        cache = state_value.get("source_file_cache")
        if isinstance(cache, dict):
            for digest, entry in cache.items():
                if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
                    errors.append("source cache contains an invalid content hash key")
                if not isinstance(entry, dict):
                    errors.append(f"source cache entry is not an object: {digest}")
                elif entry.get("parser_version") != PARSER_VERSION:
                    errors.append(f"source cache parser version mismatch: {digest}")
        for problem in _walk_forbidden_keys(state_value):
            errors.append(f"extractor state: {problem}")
    retained_automatic_bytes = 0
    protected_automatic_bytes = 0
    review_pack_bytes = 0
    total_extractor_bytes = 0
    usage: Any = None
    try:
        retained_automatic_bytes = sum(
            _path_tree_bytes(path)
            for path in batch_dirs
            if path.exists() and not path.is_symlink()
        )
        review_pack_bytes = sum(
            _path_tree_bytes(path)
            for path in pack_dirs
            if path.exists() and not path.is_symlink()
        )
        total_extractor_bytes = _path_tree_bytes(root)
        measured = measure_retained_storage(
            root, expected_boundary=boundary
        )
        protected_automatic_bytes = measured.protected_automatic_bytes
        warnings.extend(measured.storage_warnings)
        usage = shutil.disk_usage(root)
    except (ExtractorError, OSError) as exc:
        errors.append(f"cannot account extractor storage: {exc}")
    if state_value is not None:
        expected_storage = {
            "protected_automatic_bytes": protected_automatic_bytes,
            "retained_automatic_bytes": retained_automatic_bytes,
            "retained_batch_count": len(batch_dirs),
            "review_pack_bytes": review_pack_bytes,
            "total_extractor_bytes": total_extractor_bytes,
        }
        if any(
            state_value.get(field) != observed
            for field, observed in expected_storage.items()
        ):
            warnings.append("stored_storage_telemetry_stale")
    return {
        "automatic_batch_budget_bytes": MAX_AUTOMATIC_BATCH_BYTES,
        "batch_count": len(batch_dirs),
        "errors": sorted(set(errors)),
        "filesystem_free_bytes": int(usage.free) if usage is not None else None,
        "filesystem_total_bytes": int(usage.total) if usage is not None else None,
        "filesystem_used_bytes": int(usage.used) if usage is not None else None,
        "minimum_free_bytes": MIN_FILESYSTEM_FREE_BYTES,
        "protected_automatic_bytes": protected_automatic_bytes,
        "retained_automatic_bytes": retained_automatic_bytes,
        "review_pack_count": len(pack_dirs),
        "review_pack_bytes": review_pack_bytes,
        "schema_version": SCHEMA_VERSION,
        "total_extractor_bytes": total_extractor_bytes,
        "valid": not errors,
        "warnings": sorted(set(warnings)),
    }


def validate_output_root(output_root: Path) -> dict[str, Any]:
    """Validate state, all snapshots, and all review packs without repairing."""
    root = _absolute_without_following(output_root)
    _require_real_directory(root, label="output root")
    state_dir = root / "state"
    _require_real_directory(state_dir, label="state directory")
    with extractor_lock(
        root, exclusive=False, nonblocking=False, create=False
    ) as acquired:
        if not acquired:
            raise ExtractorError("could not acquire shared extractor lock")
        return _validate_root_unlocked(root)


def _review_visual_context_line(summary: Mapping[str, Any]) -> str:
    """Render one bounded visual-context summary without raw image content."""
    native_photo_count = int(summary.get("native_photo_count_max") or 0)
    parts = [
        f"{native_photo_count} native "
        f"{'photo' if native_photo_count == 1 else 'photos'}"
    ]
    collection_status = summary.get("latest_collection_status")
    if collection_status:
        parts.append(f"collection {collection_status}")
    elif summary.get("visual_event_count"):
        parts.append("no collection observation retained")
    omitted_media_count = int(
        summary.get("omitted_media_observation_count") or 0
    )
    if not summary.get("collection_history_complete", True):
        parts.append(
            "collection history is incomplete; "
            f"{omitted_media_count} older media "
            f"{'observation was' if omitted_media_count == 1 else 'observations were'} omitted"
        )
    observation_status = str(summary.get("analysis_observation_status") or "")
    attempt_count = int(summary.get("analysis_attempt_count") or 0)
    successful_count = int(summary.get("successful_analysis_count") or 0)
    distinct_count = int(
        summary.get("distinct_successful_description_count") or 0
    )
    if observation_status == "not_observed":
        parts.append("no visual-analysis event observed")
    elif observation_status == "not_attempted":
        latest_status = str(summary.get("latest_analysis_status") or "")
        if latest_status == "paused":
            parts.append("analysis paused")
        elif latest_status == "invalid_supplied_media":
            parts.append("supplied media invalid")
        else:
            parts.append(f"analysis {latest_status}")
        parts.append("no analysis call attempted")
    elif observation_status in {
        "analysed",
        "attempted_not_analysed",
        "history_incomplete",
    }:
        history_complete = bool(summary.get("analysis_history_complete"))
        retained_label = "" if history_complete else "retained "
        parts.extend(
            [
                f"analysis {summary.get('latest_analysis_status')}",
                f"{attempt_count} {retained_label}"
                f"{'analysis call' if attempt_count == 1 else 'analysis calls'}",
                (
                    f"{successful_count} {retained_label}successful description"
                    + ("s" if successful_count != 1 else "")
                    if successful_count
                    else (
                        "no successful description is present among the retained events"
                        if not history_complete
                        else "no successful description"
                    )
                ),
                f"{distinct_count} {retained_label}distinct "
                f"{'hash' if distinct_count == 1 else 'hashes'}",
            ]
        )
        if not history_complete:
            omitted_visual_count = int(
                summary.get("omitted_visual_event_count") or 0
            )
            parts.append(
                "retained visual history is incomplete; "
                f"{omitted_visual_count} older visual "
                f"{'event was' if omitted_visual_count == 1 else 'events were'} omitted"
            )
        safe_hashes = [
            value[:12]
            for value in summary.get("successful_description_sha256s") or []
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
        ]
        if safe_hashes:
            parts.append("hash " + ", ".join(safe_hashes))
    elif collection_status == "unavailable":
        parts.append("no supplied visual context retained")
    return "Visual context: " + "; ".join(parts) + "."


def _review_pack_markdown(
    *,
    pack_name: str,
    source_batch: str,
    since: str,
    until: str,
    include_open: bool,
    conversation_count: int,
    candidate_count: int,
    candidates: Sequence[Mapping[str, Any]],
) -> bytes:
    lines = [
        f"# Review pack: {pack_name}",
        "",
        "This immutable private pack contains descriptive review candidates only.",
        "Inclusion does not declare a defect and does not decide repair, pass, publish,",
        "or suppress.",
        "",
        f"- Source batch: `{source_batch}`",
        f"- Conversation start window: `[{since}, {until})`",
        f"- Open conversations included: `{str(include_open).lower()}`",
        f"- Conversations: `{conversation_count}`",
        f"- Review candidates: `{candidate_count}`",
        "",
        "No labels, model judgements, or repair decisions were added.",
    ]
    by_post_id: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        for raw_summary in candidate.get("reply_visual_context_summaries") or []:
            if not isinstance(raw_summary, dict):
                continue
            post_id = str(raw_summary.get("post_id") or "")
            if not post_id:
                continue
            summary = dict(raw_summary)
            current = by_post_id.get(post_id)
            if current is None or canonical_json_bytes(
                summary, newline=False
            ) < canonical_json_bytes(current, newline=False):
                by_post_id[post_id] = summary
    if by_post_id:
        lines.extend(["", "## Reply visual context", ""])
        for post_id in sorted(by_post_id):
            lines.append(
                f"- Turn `{post_id}` — "
                + _review_visual_context_line(by_post_id[post_id])
            )
    return ("\n".join(lines) + "\n").encode("utf-8")


def freeze_review_pack(
    *,
    output_root: Path,
    pack_name: str,
    since: str,
    until: str,
    include_open: bool = False,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze an immutable private review pack from the current snapshot."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", pack_name):
        raise ExtractorError(
            "--pack-name must contain only letters, digits, dot, underscore, or hyphen"
        )
    if pack_name.startswith(".tmp-"):
        raise ExtractorError("--pack-name must not use the temporary-directory prefix")
    since_value = parse_aware_timestamp(since, option="--since")
    until_value = parse_aware_timestamp(until, option="--until")
    if until_value <= since_value:
        raise ExtractorError("--until must be later than --since")
    since_text = str(format_utc(since_value))
    until_text = str(format_utc(until_value))
    creation_value = (
        frozen_at or datetime.now(timezone.utc)
    ).astimezone(timezone.utc).replace(microsecond=0)
    creation_text = str(format_utc(creation_value))
    root = _absolute_without_following(output_root)
    _require_real_directory(root, label="output root")
    final = root / "review-packs" / pack_name
    with _private_umask():
        with extractor_lock(
            root, exclusive=True, nonblocking=False, create=False
        ) as acquired:
            if not acquired:
                raise ExtractorError("could not acquire exclusive extractor lock")
            validation = _validate_root_unlocked(root)
            if not validation["valid"]:
                raise ExtractorError(
                    "cannot freeze from an invalid extractor root: "
                    + "; ".join(validation["errors"])
                )
            if final.exists() or final.is_symlink():
                raise ExtractorError(f"review pack already exists: {pack_name}")
            batch = _current_batch_path(root)
            assert batch is not None
            batch_manifest = _strict_read_json(batch / "manifest.json")
            assert isinstance(batch_manifest, dict)
            conversations = _load_jsonl(batch / "conversations.jsonl")
            candidates = _load_jsonl(batch / "review-candidates.jsonl")
            selected_candidates = [
                row
                for row in candidates
                if row.get("prospective_status") == "eligible"
                and (
                    (candidate_start := parse_optional_timestamp(row.get("start_time")))
                    is not None
                )
                and since_value <= candidate_start < until_value
                and int(row.get("substantive_turn_count") or 0) > 0
                and (include_open or row.get("activity_status") == "quiescent")
            ]
            selected_candidates.sort(
                key=lambda row: (
                    str(row.get("start_time") or ""),
                    str(row.get("conversation_key") or ""),
                )
            )
            keys = {str(row["conversation_key"]) for row in selected_candidates}
            selected_conversations = [
                row for row in conversations if str(row.get("conversation_key")) in keys
            ]
            selected_conversations.sort(
                key=lambda row: (
                    str(row.get("start_time") or ""),
                    str(row.get("conversation_key") or ""),
                )
            )
            conversations_data = jsonl_bytes(selected_conversations)
            candidates_data = jsonl_bytes(selected_candidates)
            markdown_data = _review_pack_markdown(
                pack_name=pack_name,
                source_batch=batch.name,
                since=since_text,
                until=until_text,
                include_open=include_open,
                conversation_count=len(selected_conversations),
                candidate_count=len(selected_candidates),
                candidates=selected_candidates,
            )
            non_manifest = {
                "conversations.jsonl": conversations_data,
                "review-candidates.jsonl": candidates_data,
                "review-pack.md": markdown_data,
            }
            output_hashes = {
                name: sha256_bytes(non_manifest[name]) for name in PACK_HASHED_FILES
            }
            pack_hash = sha256_bytes(
                canonical_json_bytes(
                    {
                        "conversations_sha256": output_hashes["conversations.jsonl"],
                        "extractor_version": EXTRACTOR_VERSION,
                        "parser_version": PARSER_VERSION,
                        "review_candidates_sha256": output_hashes[
                            "review-candidates.jsonl"
                        ],
                        "review_pack_markdown_sha256": output_hashes["review-pack.md"],
                        "schema_version": SCHEMA_VERSION,
                    },
                    newline=False,
                )
            )
            manifest_value = {
                "conversation_count": len(selected_conversations),
                "creation_timestamp": creation_text,
                "extractor_version": EXTRACTOR_VERSION,
                "include_open": include_open,
                "output_file_hashes": output_hashes,
                "pack_content_sha256": pack_hash,
                "pack_name": pack_name,
                "parser_version": PARSER_VERSION,
                "prospective_boundary": batch_manifest.get("prospective_boundary"),
                "review_candidate_count": len(selected_candidates),
                "schema_version": SCHEMA_VERSION,
                "since": since_text,
                "source_batch_creation_timestamp": batch_manifest.get(
                    "creation_timestamp"
                ),
                "source_batch_id": batch.name,
                "source_snapshot_sha256": batch_manifest.get(
                    "canonical_snapshot_sha256"
                ),
                "until": until_text,
            }
            files = {
                **non_manifest,
                "manifest.json": canonical_json_bytes(manifest_value),
            }
            temporary = Path(
                tempfile.mkdtemp(prefix=".tmp-", dir=root / "review-packs")
            )
            os.chmod(temporary, 0o700, follow_symlinks=False)
            moved = False
            committed = False
            try:
                for name in PACK_FILES:
                    _write_file(temporary / name, files[name])
                _fsync_directory(temporary)
                problems = _validate_pack_directory(
                    temporary,
                    require_immutable=False,
                    expected_boundary=str(batch_manifest.get("prospective_boundary")),
                )
                if problems:
                    raise ExtractorError(
                        "temporary review pack validation failed: "
                        + "; ".join(problems)
                    )
                os.replace(temporary, final)
                moved = True
                _fsync_directory(final.parent)
                _make_tree_read_only(final)
                current_state = read_extractor_state(root, missing_ok=False)
                assert current_state is not None
                storage = measure_retained_storage(root)
                next_state = copy.deepcopy(current_state)
                next_state.update(storage.as_state_fields())
                next_state["filesystem_free_bytes"] = int(
                    shutil.disk_usage(root).free
                )
                _atomic_write_state(root, next_state)
                committed = True
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
                if moved and not committed:
                    _remove_new_tree(final)
            return {
                "conversation_count": len(selected_conversations),
                "pack": str(final),
                "review_candidate_count": len(selected_candidates),
                "status": "frozen",
            }


def _read_existing_pseudonym_key(path: Path) -> bytes:
    descriptor = _open_regular_nofollow(path, os.O_RDONLY)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ExtractorError("source pseudonym key must be a mode-0600 regular file")
        data = os.read(descriptor, 33)
    finally:
        os.close(descriptor)
    if len(data) != 32:
        raise ExtractorError("source pseudonym key must contain exactly 32 bytes")
    return data


def _write_existing_pseudonym_key(root: Path, key: bytes) -> None:
    path = root / "state" / "pseudonym-key"
    descriptor = _open_regular_nofollow(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        offset = 0
        while offset < len(key):
            offset += os.write(descriptor, key[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def rebuild_to_new_root(
    *,
    project_dir: Path,
    source_output_root: Path,
    new_output_root: Path,
    until: str,
) -> dict[str, Any]:
    """Reparse retained logs into a new root without mutating the old root."""
    project = _absolute_without_following(project_dir)
    source_root = _absolute_without_following(source_output_root)
    new_root = _absolute_without_following(new_output_root)
    _require_real_directory(project, label="project directory")
    _require_real_directory(source_root, label="source output root")
    if new_root.exists() or new_root.is_symlink():
        raise ExtractorError("--new-output-root must not already exist")
    _require_real_directory(new_root.parent, label="new output parent")
    if _is_within(new_root, project):
        raise ExtractorError("--new-output-root must not be inside the project directory")
    if _is_within(new_root, source_root) or _is_within(source_root, new_root):
        raise ExtractorError("source and new output roots must be separate trees")
    cutoff = parse_aware_timestamp(until, option="--until")

    with extractor_lock(
        source_root,
        exclusive=False,
        nonblocking=False,
        create=False,
    ) as acquired:
        if not acquired:
            raise ExtractorError("could not acquire source-root shared lock")
        raw_state = _strict_read_json(
            source_root / "state" / "extractor-state.json"
        )
        if not isinstance(raw_state, dict):
            raise ExtractorError("source extractor state must be a JSON object")
        source_tuple = (
            raw_state.get("schema_version"),
            raw_state.get("extractor_version"),
            raw_state.get("parser_version"),
        )
        if source_tuple != REGISTERED_REBUILD_SOURCE:
            raise ExtractorError(
                "unsupported rebuild source version tuple: "
                f"{source_tuple!r}; expected {REGISTERED_REBUILD_SOURCE!r}"
            )
        boundary_text = str(raw_state.get("prospective_boundary") or "")
        boundary = parse_aware_timestamp(
            boundary_text, option="source prospective boundary"
        )
        quiescence = raw_state.get("quiescence_hours")
        if (
            not isinstance(quiescence, (int, float))
            or isinstance(quiescence, bool)
            or quiescence <= 0
        ):
            raise ExtractorError("source state has an invalid quiescence policy")
        if cutoff < boundary:
            raise ExtractorError("--until precedes the frozen prospective boundary")
        key = _read_existing_pseudonym_key(
            source_root / "state" / "pseudonym-key"
        )
        inventory = collect_source_inventory(project)
        observed_times: list[datetime] = []
        for source in inventory.files:
            parsed_records, _warnings = parse_log_records(source.complete_data)
            observed_times.extend(
                record.timestamp_value
                for record in parsed_records
                if record.timestamp_value <= cutoff
            )
        if (
            not observed_times
            or min(observed_times) > boundary
            or max(observed_times) < boundary
        ):
            raise ExtractorError(
                "retained production logs do not span the frozen prospective boundary"
            )

        with _private_umask():
            ensure_private_layout(new_root)
            _write_existing_pseudonym_key(new_root, key)
        result = run_scan(
            project_dir=project,
            output_root=new_root,
            prospective_start=boundary_text,
            until=str(format_utc(cutoff)),
            quiescence_hours=float(quiescence),
        )
        validation = validate_output_root(new_root)
        if not validation.get("valid"):
            raise ExtractorError(
                "rebuilt output root failed validation: "
                + "; ".join(validation.get("errors") or [])
            )
        return {
            "new_output_root": str(new_root),
            "prospective_boundary": boundary_text,
            "scan": result,
            "status": "rebuilt",
            "validation": validation,
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan retained production logs")
    scan_parser.add_argument("--project-dir", type=Path, required=True)
    scan_parser.add_argument("--output-root", type=Path, required=True)
    scan_parser.add_argument("--prospective-start", required=True)
    scan_parser.add_argument("--until")
    scan_parser.add_argument("--quiescence-hours", type=float, default=48.0)

    status_parser = subparsers.add_parser("status", help="print read-only status")
    status_parser.add_argument("--output-root", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="validate private outputs")
    validate_parser.add_argument("--output-root", type=Path, required=True)

    freeze_parser = subparsers.add_parser(
        "freeze-review-pack", help="freeze an immutable manual review pack"
    )
    freeze_parser.add_argument("--output-root", type=Path, required=True)
    freeze_parser.add_argument("--pack-name", required=True)
    freeze_parser.add_argument("--since", required=True)
    freeze_parser.add_argument("--until", required=True)
    freeze_parser.add_argument("--include-open", action="store_true")

    rebuild_parser = subparsers.add_parser(
        "rebuild-to-new-root",
        help="reparse retained logs into a separate empty output root",
    )
    rebuild_parser.add_argument("--project-dir", type=Path, required=True)
    rebuild_parser.add_argument("--source-output-root", type=Path, required=True)
    rebuild_parser.add_argument("--new-output-root", type=Path, required=True)
    rebuild_parser.add_argument("--until", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the selected extractor command and return its process exit status."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "scan":
            result = run_scan(
                project_dir=arguments.project_dir,
                output_root=arguments.output_root,
                prospective_start=arguments.prospective_start,
                until=arguments.until,
                quiescence_hours=arguments.quiescence_hours,
            )
            sys.stdout.buffer.write(canonical_json_bytes(result))
            return 0
        if arguments.command == "status":
            sys.stdout.buffer.write(canonical_json_bytes(get_status(arguments.output_root)))
            return 0
        if arguments.command == "validate":
            result = validate_output_root(arguments.output_root)
            sys.stdout.buffer.write(canonical_json_bytes(result))
            return 0 if result["valid"] else 1
        if arguments.command == "freeze-review-pack":
            result = freeze_review_pack(
                output_root=arguments.output_root,
                pack_name=arguments.pack_name,
                since=arguments.since,
                until=arguments.until,
                include_open=arguments.include_open,
            )
            sys.stdout.buffer.write(canonical_json_bytes(result))
            return 0
        if arguments.command == "rebuild-to-new-root":
            result = rebuild_to_new_root(
                project_dir=arguments.project_dir,
                source_output_root=arguments.source_output_root,
                new_output_root=arguments.new_output_root,
                until=arguments.until,
            )
            sys.stdout.buffer.write(canonical_json_bytes(result))
            return 0
    except (ExtractorError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unsupported command: {arguments.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
