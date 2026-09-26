"""Own digest record parsing, source references and resume-window selection.

Readers retain physical source identity, ordering, duplicate multiplicity and
resume fingerprint semantics. Callers supply current regex, record constructor,
parsers, time conversion, readers and helper callbacks explicitly; no callbacks
are stored. Discovery, context/backscan policy, resume state
persistence and report orchestration stay in the digest. Importing this module
performs no log, home/configuration, state or service access.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_log_digest_contracts import InputFileSummary, SourceReference


LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<src>[^:]+?)(?::(?P<line>\d+))? - (?P<msg>.*)$"
)
SOURCE_REFERENCE_LIMIT = 8
RESUME_FINGERPRINT_TAIL_LIMIT = 128
SAFE_SOURCE_LOGGER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}\Z")


@dataclass(frozen=True)
class Record:
    """Represent record data."""
    ts: datetime
    level: str
    src: str
    line: int
    msg: str
    path: str
    ordinal: int


def parse_prefixed_json_observation(
    message: str,
    timestamp: datetime,
    level: str,
    *,
    marker: str,
    parse_error_counter: str,
    stats: Counter[str],
    errors: List[Dict[str, Any]],
    parse_json_object: Callable[..., Dict[str, Any]],
    short_text: Callable[[Any, int], str],
    source_ref: Callable[[], SourceReference],
) -> Tuple[bool, Dict[str, Any]]:
    """Parse a matched observation, recording only encoding/parser failures.

    Return success separately from the unchanged parser result: an invalid result
    from a supplied parser must still fail in the caller's observation handling.
    Marker matching and diagnostic callbacks remain outside the caught boundary.
    """
    raw = message.split(marker + " ", 1)[1].strip()
    try:
        parsed = parse_json_object(raw.encode("utf-8"), label=marker)
    except Exception as exc:
        errors.append({
            "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": f"Malformed {marker}: {exc}: {short_text(raw, 240)}",
            "source_refs": [source_ref()],
        })
        stats[parse_error_counter] += 1
        return False, {}
    return True, parsed


def safe_source_logger(
    value: Any,
    *,
    safe_source_logger_re: re.Pattern[str],
) -> str:
    """Return a non-sensitive bounded logger/function identifier."""

    logger = str(value or "")
    return logger if safe_source_logger_re.fullmatch(logger) else "unavailable"


def record_source_ref(
    record: Record,
    input_file_indexes: Optional[Dict[str, int]] = None,
    *,
    dt_text: Callable[[datetime], str],
    safe_source_logger: Callable[[Any], str],
) -> SourceReference:
    """Return bounded location metadata for one retained physical log record."""

    index = (input_file_indexes or {}).get(record.path)
    if type(index) is int and index >= 0:
        reference: SourceReference = {
            "input_file_index": index,
            "record_number": record.ordinal,
            "timestamp": dt_text(record.ts),
            "logger": safe_source_logger(record.src),
        }
    else:
        reference = {
            "source_basename": Path(record.path).name,
            "record_number": record.ordinal,
            "timestamp": dt_text(record.ts),
            "logger": safe_source_logger(record.src),
        }
    if record.line > 0:
        reference["logged_source_line_number"] = record.line
    return reference


def bounded_source_refs(
    *collections: Any,
    limit: int = SOURCE_REFERENCE_LIMIT,
) -> Tuple[List[Dict[str, Any]], int]:
    """Merge, de-duplicate and cap source references without raw log content."""

    unique: List[Dict[str, Any]] = []
    identities: set[str] = set()
    for collection in collections:
        if isinstance(collection, dict):
            candidates = [collection]
        elif isinstance(collection, list):
            candidates = collection
        else:
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            allowed = {
                key: candidate[key]
                for key in (
                    "input_file_index",
                    "source_basename",
                    "record_number",
                    "timestamp",
                    "logger",
                    "logged_source_line_number",
                )
                if key in candidate
            }
            if not allowed or "record_number" not in allowed:
                continue
            identity = json.dumps(
                allowed,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if identity in identities:
                continue
            identities.add(identity)
            unique.append(allowed)
    omitted = max(0, len(unique) - limit)
    return unique[:limit], omitted


def record_fingerprint(
    record: Record,
    *,
    dt_text: Callable[[datetime], str],
) -> str:
    """Record fingerprint."""
    body = "\x1f".join(
        [
            dt_text(record.ts),
            record.level,
            record.src,
            str(record.line),
            record.msg,
        ]
    )
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


def resume_fingerprint_tail(
    data: Dict[str, Any],
    *,
    tail_limit: int,
) -> List[str]:
    """Return the resume fingerprint tail."""
    raw = data.get("last_log_entry_fingerprint_tail")
    if not isinstance(raw, list):
        return []
    return [
        value
        for value in raw[-tail_limit:]
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    ]


def locate_resume_fingerprint_tail(
    records: List[Record],
    tail: List[str],
    *,
    record_fingerprint: Callable[[Record], str],
) -> Optional[Tuple[int, int]]:
    """Locate the saved append-order tail, tolerating bounded rotation loss."""
    if not records or not tail:
        return None
    fingerprints = [record_fingerprint(record) for record in records]
    minimum = min(8, len(tail))
    for length in range(len(tail), minimum - 1, -1):
        needle = tail[-length:]
        limit = len(fingerprints) - length + 1
        for start in range(max(0, limit)):
            if fingerprints[start:start + length] == needle:
                return start + length, length
    return None


def resume_boundary_fingerprint_counts(data: Dict[str, Any]) -> Counter[str]:
    """Return the resume boundary fingerprint counts."""
    raw_counts = data.get("last_log_entry_fingerprint_counts")
    counts: Counter[str] = Counter()
    if isinstance(raw_counts, dict):
        for fingerprint, raw_count in raw_counts.items():
            if not fingerprint or isinstance(raw_count, bool):
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError, OverflowError):
                continue
            if count > 0:
                counts[str(fingerprint)] = count
        return counts
    for fingerprint in data.get("last_log_entry_fingerprints", []):
        if fingerprint:
            counts[str(fingerprint)] += 1
    return counts


def filter_resume_boundary_records(
    records: List[Record],
    boundary: datetime,
    processed_counts: Counter[str],
    *,
    record_fingerprint: Callable[[Record], str],
) -> List[Record]:
    """Filter resume boundary records."""
    remaining = Counter(processed_counts)
    filtered: List[Record] = []
    for record in records:
        fingerprint = record_fingerprint(record)
        if record.ts == boundary and remaining[fingerprint] > 0:
            remaining[fingerprint] -= 1
            continue
        filtered.append(record)
    return filtered


def iter_records(
    path: Path,
    *,
    log_re: re.Pattern[str],
    record_type: Callable[..., Record],
    strptime: Callable[[str, str], datetime],
) -> Iterable[Record]:
    """Yield iter records values."""
    current: Optional[Dict[str, Any]] = None
    ordinal = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = log_re.match(line)
            if m:
                if current is not None:
                    yield record_type(**current)
                ordinal += 1
                current = {
                    "ts": strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S"),
                    "level": m.group("level"),
                    "src": m.group("src").strip(),
                    "line": int(m.group("line") or 0),
                    "msg": m.group("msg"),
                    "path": str(path),
                    "ordinal": ordinal,
                }
            elif current is not None:
                current["msg"] += "\n" + line
            else:
                # Ignore leading junk before first timestamp.
                pass

    if current is not None:
        yield record_type(**current)


def read_records(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
    physical_order: bool = False,
    iter_records: Callable[[Path], Iterable[Record]],
) -> List[Record]:
    """Read structured and legacy records, removing only evidenced rotation overlap."""

    def source_records() -> Iterable[Tuple[Path, Record]]:
        for path in paths:
            if not path.exists():
                print(f"WARNING: missing log file: {path}", file=sys.stderr)
                continue
            for record in iter_records(path):
                yield path, record

    return _deduplicate_records(
        paths, source_records(), since, until,
        since_exclusive=since_exclusive, physical_order=physical_order,
    )


def _deduplicate_records(
    paths: List[Path],
    source_records: Iterable[Tuple[Path, Record]],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool,
    physical_order: bool,
) -> List[Record]:
    """Retain physical occurrences; remove only a clear adjacent-file copy."""
    by_path: Dict[str, List[Record]] = {str(path): [] for path in paths}
    for path, record in source_records:
        # Bound history scans as records arrive. If a time window cuts through
        # copied content, retain the uncertain boundary rather than guessing.
        if since is not None and (
            record.ts < since or (since_exclusive and record.ts == since)
        ):
            continue
        if until is not None and record.ts > until:
            continue
        by_path.setdefault(str(path), []).append(record)

    canonical = [
        (path, re.fullmatch(r"(?P<base>.+\.log)(?:\.(?P<rotation>\d+))?", path.name))
        for path in paths
    ]
    same_rotation_family = bool(canonical) and all(match for _path, match in canonical)
    if same_rotation_family:
        families = {(path.parent.resolve(), match.group("base")) for path, match in canonical if match}
        same_rotation_family = len(families) == 1
    if same_rotation_family:
        ordered_paths = sorted(
            paths,
            key=lambda path: -int(path.name.rsplit(".", 1)[1])
            if path.name.rsplit(".", 1)[1].isdigit() else 0,
        )
    else:
        def physical_path_key(path: Path) -> Tuple[int, str]:
            try:
                return path.stat().st_mtime_ns, str(path)
            except OSError:
                return 0, str(path)

        ordered_paths = sorted(paths, key=physical_path_key)

    def identity(record: Record) -> tuple[Any, ...]:
        return record.ts, record.level, record.src, record.line, record.msg

    def overlap_length(old_records: List[Record], new_records: List[Record]) -> int:
        """Find the longest old suffix equal to a new prefix in linear time."""
        if not old_records or not new_records:
            return 0
        new_keys = [identity(record) for record in new_records]
        prefixes = [0] * len(new_keys)
        for index in range(1, len(new_keys)):
            matched = prefixes[index - 1]
            while matched and new_keys[index] != new_keys[matched]:
                matched = prefixes[matched - 1]
            if new_keys[index] == new_keys[matched]:
                matched += 1
            prefixes[index] = matched
        matched = 0
        for record in old_records:
            key = identity(record)
            while matched and (matched == len(new_keys) or key != new_keys[matched]):
                matched = prefixes[matched - 1]
            if key == new_keys[matched]:
                matched += 1
        return matched

    if same_rotation_family:
        for older, newer in zip(ordered_paths, ordered_paths[1:]):
            older_number = int(older.name.rsplit(".", 1)[1]) if older.name.rsplit(".", 1)[1].isdigit() else 0
            newer_number = int(newer.name.rsplit(".", 1)[1]) if newer.name.rsplit(".", 1)[1].isdigit() else 0
            if older_number != newer_number + 1:
                continue
            old_records = by_path[str(older)]
            new_records = by_path[str(newer)]
            # Same-second boundary matches can be genuine repeated events.
            # Require a contiguous copy spanning distinct timestamps before
            # removing newer copies; keep the older physical cursor position.
            length = overlap_length(old_records, new_records)
            if length > 1 and len({record.ts for record in new_records[:length]}) > 1:
                del new_records[:length]

    out = [record for records in by_path.values() for record in records]
    if physical_order:
        physical_priority = {str(path): index for index, path in enumerate(ordered_paths)}
        out.sort(key=lambda record: (physical_priority.get(record.path, len(paths)), record.ordinal, record.ts))
    else:
        out.sort(key=lambda record: (record.ts, record.path, record.ordinal))
    return out


def filter_records_by_time(
    records: List[Record],
    since: Optional[datetime],
    *,
    since_exclusive: bool,
) -> List[Record]:
    """Filter records by time."""
    if since is None:
        return list(records)
    if since_exclusive:
        return [record for record in records if record.ts > since]
    return [record for record in records if record.ts >= since]


@dataclass(frozen=True)
class ResumeWindowSelection:
    """Keep selected physical records and the cursor decision together."""

    records: List[Record]
    cursor_mode: str
    tail_match_length: int
    timestamp_fallback: bool


def select_resume_window(
    physical_records: List[Record],
    since: Optional[datetime],
    *,
    since_exclusive: bool,
    saved_resume_tail: List[str],
    resume_boundary_counts: Counter[str],
    locate_resume_fingerprint_tail: Callable[..., Optional[Tuple[int, int]]],
    filter_records_by_time: Callable[..., List[Record]],
    filter_resume_boundary_records: Callable[..., List[Record]],
    warn_timestamp_fallback: Callable[[], None],
) -> ResumeWindowSelection:
    """Prefer a saved physical tail, falling back to timestamp and occurrence bounds.

    Inputs have already been read in physical order and bounded by ``until``.
    Preserve that order and the record objects. The caller's fallback warning
    runs before timestamp filtering, even if a filter fails. Callers retain
    delivery and cursor saving; inputs are not mutated and callbacks not stored.
    """
    tail_match = (
        locate_resume_fingerprint_tail(physical_records, saved_resume_tail)
        if saved_resume_tail else None
    )
    if tail_match is not None:
        cursor_end, match_length = tail_match
        return ResumeWindowSelection(
            physical_records[cursor_end:], "fingerprint_tail", match_length, False,
        )
    if saved_resume_tail:
        warn_timestamp_fallback()
    records = filter_records_by_time(
        physical_records, since, since_exclusive=since_exclusive,
    )
    if since is not None and resume_boundary_counts:
        records = filter_resume_boundary_records(records, since, resume_boundary_counts)
    return ResumeWindowSelection(records, "timestamp", 0, bool(saved_resume_tail))


def summarize_input_files(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
    iter_records: Callable[[Path], Iterable[Record]],
    fromtimestamp: Callable[[float], datetime],
    dt_text: Callable[[datetime], str],
) -> List[InputFileSummary]:
    """Summarise input files."""
    summaries: List[InputFileSummary] = []
    for _path, _record in _summarized_source_records(
        paths, since, until, summaries,
        since_exclusive=since_exclusive, warn_missing=False,
        iter_records=iter_records, fromtimestamp=fromtimestamp, dt_text=dt_text,
    ):
        pass
    return summaries


def read_records_and_summaries(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
    iter_records: Callable[[Path], Iterable[Record]],
    fromtimestamp: Callable[[float], datetime],
    dt_text: Callable[[datetime], str],
) -> Tuple[List[Record], List[InputFileSummary]]:
    """Read physical resume records and raw input summaries in one parse.

    Records retain all observations through ``until``, including those before
    ``since`` needed to locate a saved physical cursor. Summaries describe each
    complete raw source and its requested timestamp window, before deduplication
    or resume selection. Metadata is observed immediately before each file read.
    """
    summaries: List[InputFileSummary] = []
    source_records = _summarized_source_records(
        paths, since, until, summaries,
        since_exclusive=since_exclusive, warn_missing=True,
        iter_records=iter_records, fromtimestamp=fromtimestamp, dt_text=dt_text,
    )
    records = _deduplicate_records(
        paths, source_records, None, until,
        since_exclusive=False, physical_order=True,
    )
    return records, summaries


def _summarized_source_records(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    summaries: List[InputFileSummary],
    *,
    since_exclusive: bool,
    warn_missing: bool,
    iter_records: Callable[[Path], Iterable[Record]],
    fromtimestamp: Callable[[float], datetime],
    dt_text: Callable[[datetime], str],
) -> Iterable[Tuple[Path, Record]]:
    """Observe complete raw sources while yielding records for optional analysis."""

    for path in paths:
        summary: InputFileSummary = {
            "path": str(path),
            "exists": path.exists(),
            "size": None,
            "mtime": None,
            "total_records": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "records_after_since": 0,
            "records_in_window": 0,
        }

        summaries.append(summary)
        if not summary["exists"]:
            if warn_missing:
                print(f"WARNING: missing log file: {path}", file=sys.stderr)
            continue

        try:
            stat = path.stat()
            summary["size"] = stat.st_size
            summary["mtime"] = fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass

        for record in iter_records(path):
            summary["total_records"] += 1
            ts_text = dt_text(record.ts)
            if summary["first_timestamp"] is None:
                summary["first_timestamp"] = ts_text
            summary["last_timestamp"] = ts_text

            after_since = True
            if since is not None:
                after_since = record.ts > since if since_exclusive else record.ts >= since
            if after_since:
                summary["records_after_since"] += 1

            selected = after_since
            if until is not None and record.ts > until:
                selected = False
            if selected:
                summary["records_in_window"] += 1
            yield path, record


def input_retention_coverage(
    input_files: List[InputFileSummary],
    since: Optional[datetime],
    *,
    parse_dt: Callable[[str], Optional[datetime]],
    dt_text: Callable[[datetime], str],
) -> Dict[str, Any]:
    """Describe whether retained records cover the requested lower boundary."""
    timestamps: List[datetime] = []
    for item in input_files:
        first = item.get("first_timestamp")
        if not first:
            continue
        try:
            parsed = parse_dt(str(first))
        except ValueError:
            continue
        if parsed is not None:
            timestamps.append(parsed)
    earliest = min(timestamps) if timestamps else None
    result: Dict[str, Any] = {
        "requested_since": dt_text(since) if since else None,
        "earliest_retained_timestamp": dt_text(earliest) if earliest else None,
        "requested_start_covered": None,
        "retention_gap_seconds": None,
        "warning": "",
    }
    if since is None or earliest is None:
        return result
    if earliest <= since:
        result["requested_start_covered"] = True
        return result
    gap = int((earliest - since).total_seconds())
    result.update(
        {
            "requested_start_covered": False,
            "retention_gap_seconds": gap,
            "warning": (
                f"requested window starts at {dt_text(since)}, but the earliest "
                f"retained timestamp is {dt_text(earliest)}; coverage of the "
                "preceding interval cannot be verified from retained logs"
            ),
        }
    )
    return result


def combine_input_warnings(*warnings: Optional[str]) -> Optional[str]:
    """Combine distinct non-empty input warnings deterministically."""
    values = list(dict.fromkeys(str(value) for value in warnings if value))
    return "; ".join(values) if values else None
