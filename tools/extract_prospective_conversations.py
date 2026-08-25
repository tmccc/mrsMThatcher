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


SCHEMA_VERSION = 2
EXTRACTOR_VERSION = "prospective-conversation-extractor-v2"
PARSER_VERSION = "prospective-conversation-log-parser-v2"
HASH_BLOCK_SIZE = 1024 * 1024
X_SNOWFLAKE_EPOCH_MS = 1_288_834_974_657
X_SNOWFLAKE_MIN_DIGITS = 15
X_SNOWFLAKE_MAX_DIGITS = 20
X_SNOWFLAKE_FUTURE_SKEW = timedelta(minutes=5)
MAX_SEND_ATTEMPTS_PER_TARGET = 5
RECENT_BATCH_RETENTION = timedelta(hours=72)
DAILY_BATCH_RETENTION = timedelta(days=90)
DISK_RESERVED_HEADROOM_BYTES = 512 * 1024 * 1024
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
CONTEXT_RE = re.compile(
    r"Built AI reply context for (?:mention|hot.post.reply) (\d+)\. "
    r"chain_items=(\d+) immediate_parent=([^\s]+) quoted=(True|False)",
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
    "same_chain_user_continuation",
    "multiple_substantive_user_turns",
    "multiple_account_replies",
    "third_or_later_substantive_turn",
    "explicit_correction_cue",
    "post_clarification_continuation",
    "sibling_branch_activity",
    "partial_reconstruction",
    "ambiguous_parentage",
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
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + ("\n" if newline else "")).encode("utf-8")


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) for row in rows)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_BLOCK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *values: object) -> str:
    material = "\x1f".join(str(value) for value in values).encode(
        "utf-8", "surrogatepass"
    )
    return f"{prefix}-{sha256_bytes(material)}"


def parse_aware_timestamp(value: str, *, option: str) -> datetime:
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
    if value is None:
        return None
    normalised = value.astimezone(timezone.utc)
    if normalised.microsecond:
        text = normalised.isoformat(timespec="microseconds")
    else:
        text = normalised.isoformat(timespec="seconds")
    return text.replace("+00:00", "Z")


def compact_utc(value: datetime) -> str:
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
    number = _source_number(path.name)
    if number is None:
        return (1, path.name)
    return (-number, path.name)


@dataclass(frozen=True)
class SourceWarning:
    basename: str
    kind: str
    detail: str

    def as_json(self) -> dict[str, str]:
        return {"basename": self.basename, "detail": self.detail, "kind": self.kind}


@dataclass(frozen=True)
class SourceRead:
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
    candidate = value.strip()
    try:
        decoded = ast.literal_eval(candidate)
    except (SyntaxError, ValueError):
        return candidate
    return decoded if isinstance(decoded, str) else candidate


def normalise_lane(value: Any) -> str:
    lane = str(value or "other").strip().casefold().replace("_", "-")
    if "hot-post" in lane:
        return "hot-post reply"
    if lane.startswith("quote"):
        return "quote-tweet reply"
    if lane in {"mention", "normal", "normal-reply"} or lane.startswith("mention+"):
        return "mention"
    return lane or "other conversational lane"


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


def _set_explicit_creation_time(
    post: dict[str, Any],
    event: Mapping[str, Any],
    fields: Sequence[str],
    record: LogRecord,
    *,
    subject: str,
) -> None:
    supplied = [
        (field, str(event[field]).strip())
        for field in fields
        if event.get(field) is not None and str(event[field]).strip()
    ]
    if not supplied:
        _set_snowflake_creation_time(post)
        return
    if len({value for _field, value in supplied}) > 1:
        _record_warning(post, f"conflicting_explicit_{subject}_creation_times")
        _set_snowflake_creation_time(post)
        return
    field, value = supplied[0]
    try:
        parsed = parse_aware_timestamp(value, option=f"structured {field}")
    except ExtractorError:
        _record_warning(post, f"invalid_explicit_{subject}_creation_time")
        _set_snowflake_creation_time(post)
        return
    first_observed = parse_optional_timestamp(post.get("first_observed_at"))
    if (
        first_observed is not None
        and parsed > first_observed + X_SNOWFLAKE_FUTURE_SKEW
    ):
        _record_warning(post, f"implausible_explicit_{subject}_creation_time")
        _set_snowflake_creation_time(post)
        return
    post["created_at"] = format_utc(parsed)
    post["creation_time_confidence"] = "high"
    post["creation_time_source"] = "structured_event"
    post["creation_time_provenance"] = _merge_unique_objects(
        list(post.get("creation_time_provenance") or []),
        [
            {
                "event_kind": str(event.get("event") or event.get("kind") or ""),
                "field": field,
                "observed_at": record.timestamp,
                "record_fingerprint": record.record_fingerprint,
                "source": "structured_event",
            }
        ],
    )


def _blank_post(post_id: str, key: bytes, *, author_role: str = "user") -> dict[str, Any]:
    namespace = "account" if author_role == "account" else "unknown"
    raw_identity = "mrsMThatcher-account" if author_role == "account" else post_id
    return {
        "account_turn_asked_for_clarification": False,
        "author_key": _pseudonym(key, namespace, raw_identity),
        "author_role": author_role,
        "canonical_event_id": stable_id("post", post_id),
        "conversation_id": None,
        "created_at": None,
        "creation_time_confidence": "unavailable",
        "creation_time_provenance": [],
        "creation_time_source": "unavailable",
        "derivation_parser_version": PARSER_VERSION,
        "first_observed_at": None,
        "generated_reply_text": None,
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
        "reply_requirement": None,
        "root_post_id": None,
        "route_source": None,
        "schema_version": SCHEMA_VERSION,
        "send_attempts": [],
        "source_provenance": [],
        "tested_pipeline_stage_summaries": [],
        "text": None,
        "thread_id": None,
        "trusted_fact_count": None,
        "trusted_fact_ids": [],
        "warnings": [],
    }


def _confidence_rank(value: Any) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(value), 0)


def _merge_unique_objects(existing: list[Any], additions: Iterable[Any]) -> list[Any]:
    keyed = {canonical_json_bytes(item, newline=False): item for item in existing}
    for item in additions:
        keyed.setdefault(canonical_json_bytes(item, newline=False), item)
    return [keyed[key] for key in sorted(keyed)]


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


def _set_text(post: dict[str, Any], text: Any) -> None:
    candidate = str(text or "")
    if not candidate:
        return
    current = str(post.get("text") or "")
    if current and current != candidate:
        _record_warning(post, "conflicting_text_observations")
    if not current or len(candidate) > len(current):
        post["text"] = candidate


def _set_author(post: dict[str, Any], key: bytes, raw_author_id: Any) -> None:
    raw = str(raw_author_id or "")
    if not raw:
        return
    candidate = _pseudonym(key, "user", raw)
    current = str(post.get("author_key") or "")
    if current.startswith("user-") and current != candidate:
        _record_warning(post, "conflicting_pseudonymous_author_observations")
        return
    post["author_key"] = candidate


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
    }
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


def normalise_canonical_posts(
    records: Sequence[LogRecord],
    prior_posts: Sequence[Mapping[str, Any]],
    pseudonym_key: bytes,
    *,
    parser_statistics: dict[str, int] | None = None,
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
    posts: dict[str, dict[str, Any]] = {
        str(row["post_id"]): copy.deepcopy(dict(row))
        for row in prior_posts
        if row.get("post_id")
    }
    active_attempt: tuple[str, str] | None = None

    def get_user(target_id: str) -> dict[str, Any]:
        row = posts.setdefault(target_id, _blank_post(target_id, pseudonym_key))
        if row.get("author_role") not in {None, "user"}:
            _record_warning(row, "post_observed_with_conflicting_author_roles")
        else:
            row["author_role"] = "user"
            row["publication_status"] = "observed"
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
        row = posts.setdefault(
            reply_post_id,
            _blank_post(reply_post_id, pseudonym_key, author_role="account"),
        )
        row["author_role"] = "account"
        row["author_key"] = _pseudonym(
            pseudonym_key, "account", "mrsMThatcher-account"
        )
        if authority == "structured_confirmation" or not row.get(
            "publication_authority"
        ):
            row["publication_authority"] = authority
        row["publication_evidence"] = _merge_unique_objects(
            list(row.get("publication_evidence") or []),
            [
                {
                    "event_kind": evidence_kind,
                    "observed_at": record.timestamp,
                    "record_fingerprint": record.record_fingerprint,
                }
            ],
        )
        row["publication_status"] = "published"
        row["parent_post_id"] = target_id
        row["parent_observation_status"] = "observed"
        row["lane"] = target.get("lane") or "other conversational lane"
        for field in ("conversation_id", "root_post_id", "thread_id"):
            if target.get(field):
                row[field] = target[field]
        _touch_post(row, record)
        selected_attempt = _mark_send_attempt(
            target,
            record=record,
            status_value="confirmed",
            reply_post_id=reply_post_id,
            require_unambiguous=True,
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
        _set_text(row, final_text)
        if selected_attempt is None and len(target_attempts) > 1:
            _record_warning(row, "ambiguous_confirmed_reply_attempt_text")
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

    for record in ordered_records:
        message = record.message
        match = CONSIDER_RE.search(message)
        if match:
            target_id = match.group(2)
            row = get_user(target_id)
            row["lane"] = normalise_lane(match.group(1))
            _set_author(row, pseudonym_key, match.group(3))
            _set_text(row, decode_literal(match.group(4)))
            _touch_post(row, record)
            if _confidence_rank(row.get("reconstruction_confidence")) < 1:
                row["reconstruction_confidence"] = "medium"

        match = QUOTE_RE.search(message)
        if match:
            target_id = match.group(1)
            row = get_user(target_id)
            row["lane"] = "quote-tweet reply"
            _set_author(row, pseudonym_key, match.group(2))
            _set_text(row, decode_literal(match.group(4)))
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
            if parent.casefold() in {"none", "null", "unavailable", ""}:
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
            row["parent_thread_entry_count"] = chain_items
            _touch_post(row, record)

        match = GENERATED_RE.search(message)
        if match:
            target_id = match.group(2)
            row = get_user(target_id)
            row["generated_reply_text"] = decode_literal(match.group(3))
            row["generated_reply_text_observed_at"] = record.timestamp
            row["lane"] = normalise_lane(match.group(1))
            _touch_post(row, record)

        match = GENERATED_QUOTE_RE.search(message)
        if match:
            target_id = match.group(1)
            row = get_user(target_id)
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
            get_user(target_id)["lane"] = normalise_lane(match.group(1))
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
        contract = STRUCTURED_CONVERSATION_EVENT_FIELDS.get(kind)
        if contract is None:
            statistics["ignored_structured_event_count"] += 1
            if any(
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
            ):
                statistics["ignored_target_like_event_count"] += 1
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
        if event.get("lane") or event.get("candidate_source") or event.get("source"):
            target["lane"] = normalise_lane(
                event.get("lane") or event.get("candidate_source") or event.get("source")
            )
        raw_author, author_ambiguous = _registered_value(
            event, contract.author_fields
        )
        if author_ambiguous:
            _record_warning(target, "conflicting_registered_author_fields")
        elif raw_author:
            _set_author(target, pseudonym_key, raw_author)
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
                _set_text(target, event[text_field])
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

    for post in posts.values():
        post["source_provenance"] = _merge_unique_objects(
            [], post.get("source_provenance") or []
        )
        post["pipeline_stage_summaries"] = _merge_unique_objects(
            [], post.get("pipeline_stage_summaries") or []
        )
        post["tested_pipeline_stage_summaries"] = _merge_unique_objects(
            [], post.get("tested_pipeline_stage_summaries") or []
        )
        post["warnings"] = sorted(set(post.get("warnings") or []))
        post["trusted_fact_ids"] = sorted(set(post.get("trusted_fact_ids") or []))
        post["creation_time_provenance"] = _merge_unique_objects(
            [], post.get("creation_time_provenance") or []
        )
        post["publication_evidence"] = _merge_unique_objects(
            [], post.get("publication_evidence") or []
        )
        post["send_attempts"] = _bounded_attempts(post)
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


@dataclass(frozen=True)
class SubstantiveResult:
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


def _sibling_branch_activity(turns: Sequence[Mapping[str, Any]]) -> bool:
    by_id = {str(turn["post_id"]): turn for turn in turns}
    user_children: dict[str, set[str]] = defaultdict(set)
    account_targets: set[str] = set()
    for turn in turns:
        parent = str(turn.get("parent_post_id") or "")
        if turn.get("author_role") == "user" and parent:
            user_children[parent].add(str(turn["post_id"]))
        elif turn.get("author_role") == "account" and parent in by_id:
            account_targets.add(parent)
    return any(
        len(children & account_targets) >= 2 for children in user_children.values()
    )


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
        candidate = build_review_candidate(conversation)
        if candidate is not None:
            candidates.append(candidate)

    conversations.sort(
        key=lambda row: (str(row.get("start_time") or ""), row["conversation_key"])
    )
    candidates.sort(
        key=lambda row: (str(row.get("start_time") or ""), row["conversation_key"])
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


def build_review_candidate(conversation: Mapping[str, Any]) -> dict[str, Any] | None:
    if conversation.get("prospective_status") != "eligible":
        return None
    turns = list(conversation.get("turns") or [])
    user_turns = [turn for turn in turns if turn.get("author_role") == "user"]
    account_turns = [turn for turn in turns if turn.get("author_role") == "account"]
    substantive_user_turns = [turn for turn in user_turns if turn.get("substantive")]
    account_by_id = {str(turn["post_id"]): turn for turn in account_turns}
    same_chain = any(
        str(turn.get("parent_post_id") or "") in account_by_id for turn in user_turns
    )
    post_clarification = any(
        account_by_id[str(turn.get("parent_post_id"))].get(
            "account_turn_asked_for_clarification"
        )
        for turn in user_turns
        if str(turn.get("parent_post_id") or "") in account_by_id
    )
    cues = sorted(
        {
            cue
            for turn in user_turns
            for cue in turn.get("correction_cues") or []
        }
    )
    reasons: set[str] = set()
    if same_chain:
        reasons.add("same_chain_user_continuation")
    if len(substantive_user_turns) >= 2 and account_turns:
        reasons.add("multiple_substantive_user_turns")
    if len(account_turns) >= 2:
        reasons.add("multiple_account_replies")
    if int(conversation.get("substantive_turn_count") or 0) >= 3:
        reasons.add("third_or_later_substantive_turn")
    if cues:
        reasons.add("explicit_correction_cue")
    if post_clarification:
        reasons.add("post_clarification_continuation")
    if _sibling_branch_activity(turns):
        reasons.add("sibling_branch_activity")
    if conversation.get("completeness") == "partial":
        reasons.add("partial_reconstruction")
    if any("ambiguous" in str(value) for value in conversation.get("warnings") or []):
        reasons.add("ambiguous_parentage")
    if not reasons:
        return None
    ordered_reasons = [reason for reason in REVIEW_REASON_ORDER if reason in reasons]
    summaries = _flatten_pipeline_summaries(turns)
    route_sources = sorted(
        {
            str(turn["route_source"])
            for turn in turns
            if turn.get("route_source")
        }
        | {
            str(summary["route_source"])
            for summary in summaries
            if summary.get("route_source")
        }
    )
    reply_requirements = sorted(
        {
            str(turn["reply_requirement"])
            for turn in turns
            if turn.get("reply_requirement")
        }
        | {
            str(summary["reply_requirement"])
            for summary in summaries
            if summary.get("reply_requirement")
        }
    )
    trusted_fact_counts = sorted(
        {
            int(value)
            for value in [
                *(turn.get("trusted_fact_count") for turn in turns),
                *(summary.get("trusted_facts_supplied_count") for summary in summaries),
            ]
            if type(value) is int and value >= 0
        }
    )
    trusted_fact_ids = sorted(
        {
            str(value)
            for turn in turns
            for value in turn.get("trusted_fact_ids") or []
        }
        | {
            str(value)
            for summary in summaries
            for value in summary.get("trusted_fact_ids_supplied") or []
        }
    )
    principal = str(conversation.get("author_key") or "")
    same_author_depth = sum(
        turn.get("author_role") == "user" and turn.get("author_key") == principal
        for turn in turns
    )
    return {
        "account_turn_count": int(conversation.get("account_turn_count") or 0),
        "activity_status": conversation.get("activity_status"),
        "author_key": principal,
        "conversation_key": conversation["conversation_key"],
        "correction_cues": cues,
        "last_activity_time": conversation.get("last_activity_time"),
        "pipeline_stage_summaries": summaries,
        "prospective_status": "eligible",
        "reconstruction_confidence": conversation.get("reconstruction_confidence"),
        "reply_requirements": reply_requirements,
        "review_reason_codes": ordered_reasons,
        "route_sources": route_sources,
        "same_author_continuation_depth": same_author_depth,
        "schema_version": SCHEMA_VERSION,
        "start_time": conversation.get("start_time"),
        "substantive_turn_count": int(
            conversation.get("substantive_turn_count") or 0
        ),
        "trusted_fact_counts": trusted_fact_counts,
        "trusted_fact_ids": trusted_fact_ids,
        "turns": copy.deepcopy(turns),
        "user_turn_count": int(conversation.get("user_turn_count") or 0),
        "warnings": list(conversation.get("warnings") or []),
    }


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
    value = dict(state_value)
    for _attempt in range(4):
        data = canonical_json_bytes(value)
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
        if "total_extractor_bytes" not in value:
            return
        observed_total = _path_tree_bytes(root)
        if value.get("total_extractor_bytes") == observed_total:
            return
        value["total_extractor_bytes"] = observed_total
    raise ExtractorError("extractor state size accounting did not stabilise")


@contextmanager
def extractor_lock(
    root: Path,
    *,
    exclusive: bool,
    nonblocking: bool,
    create: bool,
) -> Iterator[bool]:
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
    pruned_batch_ids: tuple[str, ...]
    retained_batch_count: int
    retained_automatic_bytes: int
    review_pack_bytes: int
    total_extractor_bytes: int

    def as_state_fields(self) -> dict[str, Any]:
        return {
            "pruned_batch_ids": list(self.pruned_batch_ids),
            "retained_automatic_bytes": self.retained_automatic_bytes,
            "retained_batch_count": self.retained_batch_count,
            "review_pack_bytes": self.review_pack_bytes,
            "total_extractor_bytes": self.total_extractor_bytes,
        }


def measure_retained_storage(root: Path) -> RetentionResult:
    batches = [
        path
        for path in (root / "batches").iterdir()
        if BATCH_ID_RE.fullmatch(path.name)
    ]
    packs = list((root / "review-packs").iterdir())
    return RetentionResult(
        pruned_batch_ids=(),
        retained_batch_count=len(batches),
        retained_automatic_bytes=sum(_path_tree_bytes(path) for path in batches),
        review_pack_bytes=sum(_path_tree_bytes(path) for path in packs),
        total_extractor_bytes=_path_tree_bytes(root),
    )


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


def _review_pack_batch_references(
    root: Path,
    *,
    expected_boundary: str,
) -> tuple[set[str], int]:
    references: set[str] = set()
    total_bytes = 0
    for pack in sorted((root / "review-packs").iterdir(), key=lambda item: item.name):
        problems = _validate_pack_directory(
            pack,
            require_immutable=True,
            expected_boundary=expected_boundary,
        )
        if problems:
            raise ExtractorError(
                "review-pack provenance is invalid; retention is blocked: "
                + "; ".join(problems)
            )
        manifest = _strict_read_json(pack / "manifest.json")
        if not isinstance(manifest, dict):
            raise ExtractorError(
                f"review-pack manifest is not an object; retention is blocked: {pack}"
            )
        source_batch = str(manifest.get("source_batch_id") or "")
        if not BATCH_ID_RE.fullmatch(source_batch):
            raise ExtractorError(
                f"review-pack source batch is invalid; retention is blocked: {pack}"
            )
        references.add(source_batch)
        total_bytes += _path_tree_bytes(pack)
    return references, total_bytes


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
) -> RetentionResult:
    """Apply the fixed recent/daily retention policy under the scan lock."""
    current_target = _current_link_target(root, required=False)
    current_batch = Path(current_target).name if current_target else None
    review_references, review_bytes = _review_pack_batch_references(
        root, expected_boundary=expected_boundary
    )
    batch_rows: list[tuple[Path, datetime]] = []
    for batch in sorted((root / "batches").iterdir(), key=lambda item: item.name):
        if not BATCH_ID_RE.fullmatch(batch.name):
            raise ExtractorError(
                f"malformed or temporary batch blocks retention: {batch.name}"
            )
        problems = _validate_batch_directory(
            batch,
            require_immutable=True,
            expected_boundary=expected_boundary,
        )
        if problems:
            raise ExtractorError(
                "invalid automatic batch blocks retention: " + "; ".join(problems)
            )
        manifest = _strict_read_json(batch / "manifest.json")
        assert isinstance(manifest, dict)
        created = parse_aware_timestamp(
            str(manifest.get("creation_timestamp") or ""),
            option=f"batch {batch.name} creation timestamp",
        )
        batch_rows.append((batch, created))
    missing_references = review_references - {path.name for path, _ in batch_rows}
    if missing_references:
        raise ExtractorError(
            "review pack references missing automatic batch; retention is blocked"
        )

    protected = set(review_references)
    if current_batch:
        protected.add(current_batch)
    recent_floor = now - RECENT_BATCH_RETENTION
    daily_floor = now - DAILY_BATCH_RETENTION
    older_daily: dict[str, tuple[Path, datetime]] = {}
    for batch, created in batch_rows:
        if created >= recent_floor:
            protected.add(batch.name)
        elif created >= daily_floor:
            day = created.strftime("%Y-%m-%d")
            previous = older_daily.get(day)
            if previous is None or (created, batch.name) > (
                previous[1],
                previous[0].name,
            ):
                older_daily[day] = (batch, created)
    protected.update(batch.name for batch, _created in older_daily.values())

    candidates = [
        batch for batch, _created in batch_rows if batch.name not in protected
    ]
    for batch in candidates:
        if batch.name == current_batch or batch.name in review_references:
            raise ExtractorError(f"internal retention protection failure: {batch.name}")
    for batch in candidates:
        _delete_retained_batch(batch)

    retained = sorted(
        (
            path
            for path in (root / "batches").iterdir()
            if BATCH_ID_RE.fullmatch(path.name)
        ),
        key=lambda item: item.name,
    )
    automatic_bytes = sum(_path_tree_bytes(path) for path in retained)
    return RetentionResult(
        pruned_batch_ids=tuple(batch.name for batch in candidates),
        retained_batch_count=len(retained),
        retained_automatic_bytes=automatic_bytes,
        review_pack_bytes=review_bytes,
        total_extractor_bytes=_path_tree_bytes(root),
    )


def _build_extraction_report(
    *,
    boundary: str,
    cutoff: str,
    counts: Mapping[str, int],
    coverage: bool,
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
    for post in posts:
        post_id = str(post.get("post_id") or "")
        if post.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"canonical post schema mismatch for {post_id} in {batch}")
        if post.get("derivation_parser_version") != PARSER_VERSION:
            errors.append(f"canonical post parser mismatch for {post_id} in {batch}")
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
        elif parse_optional_timestamp(post.get("created_at")) is None:
            errors.append(f"authoritative creation time is missing for {post_id}")
        if post.get("publication_status") == "published" and post.get(
            "author_role"
        ) == "account":
            if post.get("publication_authority") not in {
                "structured_confirmation",
                "confirmed_receipt_promotion",
            }:
                errors.append(
                    f"published account post lacks authoritative publication source: {post_id}"
                )
            evidence = post.get("publication_evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(
                    f"published account post lacks publication evidence: {post_id}"
                )
    conversation_order = [
        (str(row.get("start_time") or ""), str(row.get("conversation_key") or ""))
        for row in conversations
    ]
    if conversation_order != sorted(conversation_order):
        errors.append(f"conversation ordering is invalid in {batch}")
    for conversation in conversations:
        turns = list(conversation.get("turns") or [])
        order = [_turn_order_key(turn) for turn in turns]
        if order != sorted(order):
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
    for candidate in candidates:
        if candidate.get("prospective_status") != "eligible":
            errors.append(f"non-eligible review candidate in {batch}")
        prohibited = {
            "defect",
            "bad_reply",
            "proposition_substitution",
            "repair_required",
            "false_concession",
        }
        if prohibited & set(candidate.get("review_reason_codes") or []):
            errors.append(f"prohibited adjudicative review reason in {batch}")
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
            parser_statistics: dict[str, int] = {}
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
            warnings = sorted(set(warnings))

            try:
                retention_before = apply_batch_retention(
                    root,
                    now=started,
                    expected_boundary=boundary_text,
                )
            except ExtractorError as exc:
                if state_value is not None:
                    failed_state = copy.deepcopy(dict(state_value))
                    failed_warnings = set(failed_state.get("warnings") or [])
                    failed_warnings.add("automatic_batch_retention_failed")
                    failed_state["warnings"] = sorted(failed_warnings)
                    failed_state["last_retention_error"] = str(exc)
                    failed_state["last_scan_start"] = str(format_utc(started))
                    failed_state["last_scan_completion"] = str(
                        format_utc(datetime.now(timezone.utc).replace(microsecond=0))
                    )
                    _atomic_write_state(root, failed_state)
                raise

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
            filesystem_free_bytes = int(shutil.disk_usage(root).free)

            next_state = {
                "counts": counts,
                "current_snapshot_hash": snapshot_hash,
                "extractor_version": EXTRACTOR_VERSION,
                "filesystem_free_bytes": filesystem_free_bytes,
                "last_scan_completion": completed_text,
                "last_scan_cutoff": cutoff_text,
                "last_scan_start": started_text,
                "last_successful_batch": (
                    batch_id
                    if state_value is None
                    or snapshot_hash != state_value.get("current_snapshot_hash")
                    else state_value.get("last_successful_batch")
                ),
                "last_successful_completion": completed_text,
                "latest_source_timestamp": parsed.latest_source_timestamp,
                "last_retention_error": None,
                "parser_version": PARSER_VERSION,
                "projected_batch_bytes": 0,
                "prospective_boundary": boundary_text,
                "quiescence_hours": float(quiescence_hours),
                "required_free_bytes": 0,
                "schema_version": SCHEMA_VERSION,
                "source_file_cache": parsed.source_cache,
                "source_file_count": len(inventory.files),
                "warnings": warnings,
                **retention_before.as_state_fields(),
            }

            unchanged = bool(
                state_value is not None
                and snapshot_hash == state_value.get("current_snapshot_hash")
            )
            if unchanged:
                _atomic_write_state(root, next_state)
                return {
                    "batch": state_value.get("last_successful_batch"),
                    "counts": counts,
                    "message": "prospective conversation corpus unchanged",
                    "snapshot_hash": snapshot_hash,
                    "status": "no_change",
                }

            source_manifest_value = {
                "extractor_version": EXTRACTOR_VERSION,
                "inventory_retry_count": inventory.retry_count,
                "ordering": "mrsMThatcher.log.100 through .1, then mrsMThatcher.log",
                "parser_version": PARSER_VERSION,
                "parse_warnings": list(parsed.warnings),
                "parsed_source_hash_count": parsed.parsed_source_hash_count,
                "prospective_boundary": boundary_text,
                "reused_source_hash_count": parsed.reused_source_hash_count,
                "schema_version": SCHEMA_VERSION,
                "source_files": [source.manifest_row() for source in inventory.files],
                "source_warnings": [warning.as_json() for warning in inventory.warnings],
                "structured_event_statistics": dict(sorted(parser_statistics.items())),
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
                "warnings": warnings,
            }
            status_data = canonical_json_bytes(status_value)
            report_data = _build_extraction_report(
                boundary=boundary_text,
                cutoff=cutoff_text,
                counts=counts,
                coverage=coverage,
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
            manifest_value = {
                "canonical_snapshot_sha256": snapshot_hash,
                "counts": counts,
                "creation_timestamp": started_text,
                "extractor_version": EXTRACTOR_VERSION,
                "output_file_hashes": output_hashes,
                "parser_version": PARSER_VERSION,
                "previous_batch_id": previous_batch,
                "prospective_boundary": boundary_text,
                "quiescence_hours": float(quiescence_hours),
                "repository_commit_sha": _read_repository_commit(project),
                "scan_cutoff": cutoff_text,
                "schema_version": SCHEMA_VERSION,
                "source_coverage_reaches_prospective_boundary": coverage,
                "source_manifest_sha256": output_hashes["source-manifest.json"],
                "warnings": warnings,
            }
            all_files = {
                **non_manifest_files,
                "manifest.json": canonical_json_bytes(manifest_value),
            }
            projected_batch_bytes = sum(len(data) for data in all_files.values())
            filesystem_free_bytes = int(shutil.disk_usage(root).free)
            required_free_bytes = (
                projected_batch_bytes + DISK_RESERVED_HEADROOM_BYTES
            )
            next_state.update(
                {
                    "filesystem_free_bytes": filesystem_free_bytes,
                    "projected_batch_bytes": projected_batch_bytes,
                    "required_free_bytes": required_free_bytes,
                }
            )
            if filesystem_free_bytes < required_free_bytes:
                if state_value is not None:
                    failed_state = copy.deepcopy(dict(state_value))
                    failed_warnings = set(failed_state.get("warnings") or [])
                    failed_warnings.add("disk_space_preflight_failed")
                    failed_state.update(retention_before.as_state_fields())
                    failed_state.update(
                        {
                            "filesystem_free_bytes": filesystem_free_bytes,
                            "last_scan_completion": completed_text,
                            "last_scan_start": started_text,
                            "projected_batch_bytes": projected_batch_bytes,
                            "required_free_bytes": required_free_bytes,
                            "warnings": sorted(failed_warnings),
                        }
                    )
                    _atomic_write_state(root, failed_state)
                raise ExtractorError(
                    "insufficient free space for prospective snapshot: "
                    f"filesystem_free_bytes={filesystem_free_bytes} "
                    f"projected_batch_bytes={projected_batch_bytes} "
                    f"required_free_bytes={required_free_bytes}"
                )

            def finalise_published_state() -> Mapping[str, Any]:
                retained = apply_batch_retention(
                    root,
                    now=started,
                    expected_boundary=boundary_text,
                )
                return {
                    **retained.as_state_fields(),
                    "filesystem_free_bytes": int(shutil.disk_usage(root).free),
                }

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
                "status": "published",
            }


def uninitialised_status() -> dict[str, Any]:
    return {
        "canonical_post_count": 0,
        "current_snapshot_hash": None,
        "extractor_version": EXTRACTOR_VERSION,
        "filesystem_free_bytes": None,
        "initialised": False,
        "last_scan_completion": None,
        "last_scan_start": None,
        "last_successful_batch": None,
        "latest_source_timestamp": None,
        "open_conversation_count": 0,
        "parser_version": PARSER_VERSION,
        "projected_batch_bytes": 0,
        "pruned_batch_ids": [],
        "prospective_boundary": None,
        "prospective_eligible_conversation_count": 0,
        "quiescent_conversation_count": 0,
        "reconstructed_conversation_count": 0,
        "required_free_bytes": 0,
        "retained_automatic_bytes": 0,
        "retained_batch_count": 0,
        "review_candidate_count": 0,
        "review_pack_bytes": 0,
        "schema_version": SCHEMA_VERSION,
        "source_file_count": 0,
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
    state_value = read_extractor_state(root, missing_ok=False)
    assert state_value is not None
    counts = state_value.get("counts")
    if not isinstance(counts, dict):
        raise ExtractorError("extractor state counts must be an object")
    result = uninitialised_status()
    result.update(
        {
            "canonical_post_count": int(counts.get("canonical_post_count") or 0),
            "current_snapshot_hash": state_value.get("current_snapshot_hash"),
            "extractor_version": state_value.get("extractor_version"),
            "filesystem_free_bytes": state_value.get("filesystem_free_bytes"),
            "initialised": True,
            "last_scan_completion": state_value.get("last_scan_completion"),
            "last_scan_start": state_value.get("last_scan_start"),
            "last_successful_batch": state_value.get("last_successful_batch"),
            "latest_source_timestamp": state_value.get("latest_source_timestamp"),
            "open_conversation_count": int(
                counts.get("open_conversation_count") or 0
            ),
            "parser_version": state_value.get("parser_version"),
            "projected_batch_bytes": int(
                state_value.get("projected_batch_bytes") or 0
            ),
            "pruned_batch_ids": list(state_value.get("pruned_batch_ids") or []),
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
            "review_candidate_count": int(
                counts.get("review_candidate_count") or 0
            ),
            "required_free_bytes": int(
                state_value.get("required_free_bytes") or 0
            ),
            "retained_automatic_bytes": int(
                state_value.get("retained_automatic_bytes") or 0
            ),
            "retained_batch_count": int(
                state_value.get("retained_batch_count") or 0
            ),
            "review_pack_bytes": int(state_value.get("review_pack_bytes") or 0),
            "source_file_count": int(state_value.get("source_file_count") or 0),
            "total_extractor_bytes": int(
                state_value.get("total_extractor_bytes") or 0
            ),
            "warnings": list(state_value.get("warnings") or []),
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
    review_pack_bytes = 0
    total_extractor_bytes = 0
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
    except (ExtractorError, OSError) as exc:
        errors.append(f"cannot account extractor storage: {exc}")
    if state_value is not None:
        expected_storage = {
            "retained_automatic_bytes": retained_automatic_bytes,
            "retained_batch_count": len(batch_dirs),
            "review_pack_bytes": review_pack_bytes,
            "total_extractor_bytes": total_extractor_bytes,
        }
        for field, observed in expected_storage.items():
            if state_value.get(field) != observed:
                errors.append(f"extractor state storage field is inaccurate: {field}")
    return {
        "batch_count": len(batch_dirs),
        "errors": sorted(set(errors)),
        "retained_automatic_bytes": retained_automatic_bytes,
        "review_pack_count": len(pack_dirs),
        "review_pack_bytes": review_pack_bytes,
        "schema_version": SCHEMA_VERSION,
        "total_extractor_bytes": total_extractor_bytes,
        "valid": not errors,
        "warnings": warnings,
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


def _review_pack_markdown(
    *,
    pack_name: str,
    source_batch: str,
    since: str,
    until: str,
    include_open: bool,
    conversation_count: int,
    candidate_count: int,
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
