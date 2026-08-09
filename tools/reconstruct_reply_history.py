#!/usr/bin/env python3
"""Reconstruct conversational reply history from read-only ZFS snapshots.

The extractor deliberately has no network or service dependencies.  It reads a
small allow-list of files at each source project root and writes a deterministic
forensic corpus to a new, private output directory.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
TOOL_VERSION = "reply-history-reconstruction-v1"
LOG_NAME_RE = re.compile(r"mrsMThatcher\.log(?:\.([0-9]+))?\Z")
LOG_HEADER_RE = re.compile(
    rb"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    rb"(?P<level>[A-Z]+)\s+(?P<src>.+?)(?::(?P<line>\d+))? - "
    rb"(?P<msg>.*?)(?:\r?\n)?\Z"
)
TIMESTAMP_PREFIX_RE = re.compile(rb"^\d{4}-\d{2}-\d{2}[ T]")
SNAPSHOT_TIME_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[-_T](?P<hour>\d{2})(?P<minute>\d{2})(?:\d{2})?"
)
VERSION_CONSTANTS = (
    "STRATEGY_VERSION",
    "PROPOSER_PROMPT_VERSION",
    "REVIEWER_PROMPT_VERSION",
    "NO_REPLY_REVIEW_PROMPT_VERSION",
    "CLAIM_AUDITOR_PROMPT_VERSION",
)
EXPLICIT_JSON_FILES = (
    "confirmed_reply_receipt.json",
)
FINGERPRINT_FILES = ("reply_strategy.py", "mrsMThatcher2.py", "bot_state.json")
OUTPUT_FILES = (
    "run_manifest.json",
    "source_files.jsonl",
    "snapshot_versions.jsonl",
    "unique_log_records.jsonl",
    "record_occurrences.jsonl",
    "conversational_candidates.jsonl",
    "routine_skips.jsonl",
    "unmatched_reply_records.jsonl",
    "reconstruction_gaps.json",
    "coverage_report.md",
    "reply_quality_inventory.json",
    "reply_quality_inventory.md",
    "SHA256SUMS",
)
ROUTINE_REASON_LABELS = (
    "spacing",
    "cap",
    "cooldown",
    "already_replied",
    "own_account",
    "already_seen",
    "lane_not_due",
)
CONVERSATIONAL_LANES = frozenset({"mention", "hot-post", "quote-tweet"})
LOCAL_REJECTION_REASONS = frozenset(
    {
        "clarification_not_direct_factual_answer",
        "clarification_thread_terminal",
        "exact_duplicate_reply",
        "near_duplicate_reply",
        "reply_not_permitted",
        "spam_or_not_worth_replying",
        "target_does_not_directly_mention_account",
    }
)
EDITORIAL_SKIP_REASONS = frozenset(
    {
        "no_usable_reply_generated",
        "reviewer_approval_missing",
    }
)
OPERATIONAL_SKIP_REASONS = frozenset(
    {
        "ai_reply_persistence_validation_failed",
        "context_unavailable",
        "reply_evidence_unavailable",
    }
)
NON_TERMINAL_REASONS = frozenset({"strategy_disabled"})
LONDON = ZoneInfo("Europe/London")
LEGACY_REPLY_FUNCTION_PREFIXES = (
    "maybe_reply_to_mentions",
    "maybe_reply_to_quote_tweets",
    "generate_ai_first_reply",
    "reply_strategy",
    "conversational_reply",
)
LEGACY_REPLY_MESSAGE_PREFIXES = (
    "AI-first reply ",
    "Conversational reply ",
    "Considering mention ",
    "Considering hot_post_reply ",
    "Considering quote tweet ",
    "Generated reply to mention ",
    "Generated reply to quote tweet ",
    "Reply posted",
    "Quote-tweet reply posted",
    "No usable reply generated for ",
    "Skipping mention ",
    "Skipping hot_post_reply ",
    "Skipping hot-post candidate ",
    "Skipping quote tweet ",
    "Skipping conversational reply ",
    "Wrote confirmed reply receipt",
    "Wrote conversational reply ",
    "Promoted conversational reply ",
    "Removed conversational reply ",
    "Reconciling confirmed reply receipt",
    "Confirmed conversational reply ",
    "Hot-post reply ",
    "Mention reply ",
    "Reply candidate ",
    "Reply strategy ",
)
DIAGNOSTIC_PHRASES = (
    "well noted",
    "is noted",
    "is welcome",
    "is appreciated",
    "thank you",
    "thanks for",
    "point taken",
    "important reminder",
)
STOP_WORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had has "
    "have he her hers him his i if in into is it its me my of on or our ours she should "
    "so than that the their theirs them they this those to too us was we were what when "
    "where which who will with would you your yours".split()
)
WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|\Z)")


class ExtractionError(RuntimeError):
    """A safe, user-facing extraction refusal."""


def json_text(value: Any, *, pretty: bool = False) -> str:
    """Return stable JSON which can represent surrogate-escaped log bytes."""
    if pretty:
        return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, *values: object) -> str:
    body = "\x1f".join(str(value) for value in values).encode("utf-8", "surrogatepass")
    return f"{prefix}-{sha256_bytes(body)}"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def normalise_created_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ExtractionError(f"--created-at is not valid ISO-8601: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExtractionError("--created-at must include a UTC offset or Z")
    if parsed.utcoffset().total_seconds() != 0:
        raise ExtractionError("--created-at must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_timestamp(value: Any, *, assume_london: bool = True) -> str:
    """Normalise an ISO timestamp to UTC, treating naive bot times as London."""
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return ""
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=LONDON if assume_london else timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_relative_project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ExtractionError("--project-relative-path must be a non-empty relative path without '..'")
    return path


def inferred_snapshot_time(name: str) -> tuple[str | None, str]:
    match = SNAPSHOT_TIME_RE.search(name)
    if not match:
        return None, "unavailable"
    raw = f"{match.group('date')}T{match.group('hour')}:{match.group('minute')}:00"
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return None, "unavailable"
    return raw, "snapshot-name embedded local date and time; timezone unavailable"


def snapshot_sort_key(name: str) -> tuple[int, str, str]:
    inferred, _method = inferred_snapshot_time(name)
    return (0 if inferred else 1, inferred or "", name)


def discover_snapshot_projects(snapshot_root: Path, relative: Path) -> tuple[list[str], dict[str, Path], list[dict[str, str]]]:
    discovered: list[str] = []
    projects: dict[str, Path] = {}
    skipped: list[dict[str, str]] = []
    try:
        entries = list(os.scandir(snapshot_root))
    except OSError as exc:
        raise ExtractionError(f"cannot enumerate snapshot root {snapshot_root}: {exc}") from exc
    for entry in sorted(entries, key=lambda item: snapshot_sort_key(item.name)):
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        discovered.append(entry.name)
        snapshot_dir = Path(entry.path).resolve(strict=True)
        project = snapshot_dir / relative
        try:
            resolved = project.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            skipped.append({"snapshot": entry.name, "reason": f"project unavailable: {type(exc).__name__}"})
            continue
        if not is_within(resolved, snapshot_dir):
            skipped.append({"snapshot": entry.name, "reason": "project path escapes snapshot"})
            continue
        if not resolved.is_dir():
            skipped.append({"snapshot": entry.name, "reason": "project path is not a directory"})
            continue
        projects[entry.name] = resolved
    return discovered, projects, skipped


def select_snapshots(
    discovered: list[str],
    projects: dict[str, Path],
    requested: list[str],
    maximum: int | None,
) -> list[str]:
    if maximum is not None and maximum < 1:
        raise ExtractionError("--max-snapshots must be at least 1")
    if requested:
        duplicate_names = sorted(name for name, count in Counter(requested).items() if count > 1)
        if duplicate_names:
            raise ExtractionError(f"duplicate --snapshot selection: {', '.join(duplicate_names)}")
        missing = [name for name in requested if name not in discovered]
        if missing:
            raise ExtractionError(f"requested snapshot not found: {', '.join(missing)}")
        unavailable = [name for name in requested if name not in projects]
        if unavailable:
            raise ExtractionError(f"requested snapshot project is inaccessible: {', '.join(unavailable)}")
        selected = sorted(requested, key=snapshot_sort_key)
    else:
        selected = sorted(projects, key=snapshot_sort_key)
    if maximum is not None:
        selected = selected[:maximum]
    return selected


def validate_output_path(
    output: Path,
    snapshot_root: Path,
    snapshot_projects: Iterable[Path],
    live_project: Path | None,
    worktree: Path,
) -> Path:
    resolved = output.expanduser().resolve(strict=False)
    protected = [snapshot_root, worktree, *snapshot_projects]
    if live_project is not None:
        protected.append(live_project)
    for root in protected:
        if is_within(resolved, root.resolve(strict=True)):
            raise ExtractionError(f"output path is inside protected source tree: {root}")
    if resolved.exists():
        if not resolved.is_dir():
            raise ExtractionError(f"output path exists and is not a directory: {resolved}")
        try:
            next(resolved.iterdir())
        except StopIteration:
            pass
        except OSError as exc:
            raise ExtractionError(f"cannot inspect output directory {resolved}: {exc}") from exc
        else:
            raise ExtractionError(f"refusing to overwrite non-empty output directory: {resolved}")
    return resolved


def safe_read_bytes(path: Path, project: Path) -> bytes:
    """Read one allow-listed source after proving its target remains in project."""
    lowered = path.name.lower()
    if lowered == ".env" or lowered.startswith(".env.") or any(
        marker in lowered for marker in ("credential", "private_key", "docker-compose", "dockerfile")
    ):
        raise ExtractionError(f"refusing prohibited source file: {path.name}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExtractionError(f"cannot resolve source file {path}: {exc}") from exc
    if not is_within(resolved, project):
        raise ExtractionError(f"source symlink escapes project: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ExtractionError(f"cannot open source read-only {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExtractionError(f"source is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def source_sort_key(name: str) -> tuple[int, int, str]:
    match = LOG_NAME_RE.fullmatch(name)
    if match:
        # A project log is one stream: oldest/highest rotation to active.
        suffix = int(match.group(1)) if match.group(1) is not None else 0
        return (0, -suffix, name)
    return (1, 0, name)


def source_candidates(project: Path) -> list[Path]:
    allowed_fixed = set(FINGERPRINT_FILES) | set(EXPLICIT_JSON_FILES)
    names: set[str] = set()
    try:
        entries = list(os.scandir(project))
    except OSError as exc:
        raise ExtractionError(f"cannot enumerate source project {project}: {exc}") from exc
    for entry in entries:
        # Select by directory entry name only. In particular, do not call
        # exists()/is_file() here because those operations follow a symlink
        # before safe_read_bytes() has proved its target stays in-project.
        if entry.name in allowed_fixed or LOG_NAME_RE.fullmatch(entry.name):
            names.add(entry.name)
    return [project / name for name in sorted(names, key=source_sort_key)]


def warning(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **fields}


def parse_log_records(
    data: bytes,
    source_key: str,
    pair_counts: Counter[tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    source_warnings: list[dict[str, Any]] = []
    current: list[bytes] = []
    current_header: re.Match[bytes] | None = None
    current_warnings: list[dict[str, Any]] = []
    stream_pair_counts: Counter[tuple[str, str]] = pair_counts if pair_counts is not None else Counter()

    def flush() -> None:
        nonlocal current, current_header, current_warnings
        if current_header is None:
            return
        raw = b"".join(current)
        raw_hash = sha256_bytes(raw)
        original_timestamp = current_header.group("ts").decode("ascii")
        timestamp = utc_timestamp(original_timestamp)
        pair = (original_timestamp, raw_hash)
        stream_pair_counts[pair] += 1
        ordinal = stream_pair_counts[pair]
        source_metadata = current_header.group("src").decode("utf-8", "surrogateescape").strip()
        line_raw = current_header.group("line")
        first_line = current[0]
        header_message = current_header.group("msg")
        first_line_ending = b"\r\n" if first_line.endswith(b"\r\n") else b"\n" if first_line.endswith(b"\n") else b""
        continuation = b"".join(current[1:])
        message_bytes = header_message + ((first_line_ending + continuation) if continuation else b"")
        message = message_bytes.decode("utf-8", "surrogateescape")
        record_warnings = list(current_warnings)
        if not raw.endswith((b"\n", b"\r")):
            record_warnings.append(warning("unterminated_final_record"))
        structured: dict[str, Any] | None = None
        if message.startswith("EVENT "):
            try:
                value = json.loads(message[6:])
                if not isinstance(value, dict):
                    raise ValueError("EVENT payload is not an object")
                structured = value
            except (json.JSONDecodeError, ValueError) as exc:
                record_warnings.append(warning("malformed_structured_event", detail=str(exc)))
        record_id = stable_id("record", original_timestamp, raw_hash, ordinal)
        records.append(
            {
                "level": current_header.group("level").decode("ascii"),
                "line": int(line_raw) if line_raw else None,
                "message": message,
                "original_timestamp_text": original_timestamp,
                "pair_ordinal": ordinal,
                "parse_warnings": record_warnings,
                "raw_record_sha256": raw_hash,
                "raw_record_text": raw.decode("utf-8", "surrogateescape"),
                "record_id": record_id,
                "source_metadata": source_metadata,
                "structured_event": structured,
                "timestamp": timestamp,
            }
        )
        current = []
        current_header = None
        current_warnings = []

    for line_number, raw_line in enumerate(data.splitlines(keepends=True), start=1):
        match = LOG_HEADER_RE.match(raw_line)
        if match:
            flush()
            current = [raw_line]
            current_header = match
            current_warnings = []
            continue
        malformed_prefix = TIMESTAMP_PREFIX_RE.match(raw_line) is not None
        if current_header is None:
            item = warning(
                "malformed_timestamp_prefix" if malformed_prefix else "leading_unparsed_material",
                byte_sha256=sha256_bytes(raw_line),
                line=line_number,
                source=source_key,
            )
            source_warnings.append(item)
        else:
            current.append(raw_line)
            if malformed_prefix:
                current_warnings.append(
                    warning("malformed_timestamp_prefix_in_record", line=line_number)
                )
    # splitlines() returns nothing for empty input and retains a non-newline tail.
    flush()
    if data and not records and not source_warnings:
        source_warnings.append(warning("unparsed_source_material", byte_sha256=sha256_bytes(data), source=source_key))
    return records, source_warnings


def decode_literal(value: str) -> str:
    text = value.strip()
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text
    return parsed if isinstance(parsed, str) else text


def normalise_lane(value: Any) -> str:
    lane = str(value or "unavailable").strip().lower().replace("_", "-")
    if "hot-post" in lane:
        return "hot-post"
    if lane in {"quote", "quote-tweet", "quote-tweet-reply"}:
        return "quote-tweet"
    if lane in {"mention", "normal", "normal-reply"} or lane.startswith("mention+"):
        return "mention"
    if "historical" in lane and "context" in lane:
        return "historical-context"
    return lane or "unavailable"


def target_from_event(event: dict[str, Any]) -> str:
    for key in ("target_id", "mention_id", "hot_post_reply_id", "quote_tweet_id", "parent_post_id", "id"):
        value = event.get(key)
        if value is not None and str(value):
            return str(value)
    return ""


def event_lane(kind: str, event: dict[str, Any]) -> str:
    if kind == "mention_reply_posted" or kind == "mention_grok_skip":
        return "mention"
    if kind.startswith("hot_post"):
        return "hot-post"
    if kind.startswith("quote_tweet"):
        return "quote-tweet"
    return normalise_lane(event.get("lane") or event.get("candidate_source") or event.get("source"))


def make_evidence_event(kind: str, record: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "original_timestamp_text": record.get("original_timestamp_text"),
        "record_id": record["record_id"],
        "timestamp": record["timestamp"],
        **fields,
    }


def is_reply_related_structured_kind(kind: str) -> bool:
    lowered = kind.strip().lower()
    return "reply" in lowered and lowered.startswith(
        (
            "ai_reply_",
            "confirmed_reply_",
            "conversational_reply_",
            "hot_post_reply_",
            "mention_reply_",
            "quote_tweet_reply_",
            "reply_",
        )
    )


def structured_evidence(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relevant: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    recognised = {
        "reply_strategy_decision",
        "reply_strategy_outcome",
        "reply_strategy_failure",
        "ai_reply_pipeline_decision",
        "ai_reply_pipeline_outcome",
        "ai_reply_pipeline_failure",
        "mention_reply_posted",
        "hot_post_reply_posted",
        "quote_tweet_reply_posted",
        "reply_posted",
        "mention_grok_skip",
        "hot_post_reply_grok_skip",
        "candidate_skipped",
        "reply_target_terminal",
        "reply_strategy_local_rejection",
        "reply_strategy_rejection",
        "reply_evidence_unavailable",
        "confirmed_reply_receipt_sending",
        "confirmed_reply_receipt_promoted",
        "confirmed_reply_receipt_removed",
        "confirmed_reply_receipt_reconciled",
    }
    for record in records:
        event = record.get("structured_event")
        if not isinstance(event, dict):
            continue
        kind = str(event.get("event") or event.get("kind") or "")
        recognised_kind = kind in recognised or (
            "hot_post" in kind and "reply" in kind and ("outcome" in kind or "posted" in kind)
        )
        if not recognised_kind:
            if is_reply_related_structured_kind(kind):
                unmatched.append(
                    {
                        "event_kind": kind or "unavailable",
                        "message_class": "unrecognised_structured_reply_event",
                        "reason": "structured reply-related event kind is not supported",
                        "record_id": record["record_id"],
                        "timestamp": record["timestamp"],
                    }
                )
            continue
        fields = dict(event)
        fields.pop("event", None)
        fields.pop("kind", None)
        fields["lane"] = event_lane(kind, event)
        fields["target_id"] = target_from_event(event)
        item = make_evidence_event(kind, record, fields)
        if fields["target_id"] and fields["lane"] in CONVERSATIONAL_LANES:
            relevant.append(item)
        elif fields["target_id"]:
            # Structured regular-post, meme and historical-context evidence is
            # outside this deliberately conversational corpus.
            continue
        else:
            unmatched.append(
                {
                    "event_kind": kind,
                    "message_class": "structured_reply_event_missing_target",
                    "reason": "structured reply event lacks explicit target identity",
                    "record_id": record["record_id"],
                    "timestamp": record["timestamp"],
                }
            )
    return relevant, unmatched


def is_reply_related_legacy_record(record: dict[str, Any]) -> bool:
    message = str(record.get("message") or "")
    if message.startswith("EVENT "):
        return False
    if message.startswith(LEGACY_REPLY_MESSAGE_PREFIXES):
        return True
    source = str(record.get("source_metadata") or "").split(".")[-1]
    return source.startswith(LEGACY_REPLY_FUNCTION_PREFIXES) and message.startswith(
        ("Candidate reply ", "Pipeline reply ")
    )


def legacy_evidence(
    records: list[dict[str, Any]],
    pending: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Recognise only legacy forms established in mrs_log_digest.py."""
    events: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    if pending is None:
        pending = {}

    def add(record: dict[str, Any], kind: str, **fields: Any) -> None:
        events.append(make_evidence_event(kind, record, fields))

    for record in records:
        message = record["message"]
        routine_forms = {
            "Daily generated/replied cap reached": ("unavailable", "cap"),
            "Skipping mention check: minimum interval between replies not reached": ("mention", "spacing"),
            "Skipping quote-tweet check: total daily reply cap reached": ("quote-tweet", "cap"),
            "Skipping quote-tweet check: daily quote-reply cap reached": ("quote-tweet", "cap"),
        }
        if message in routine_forms:
            lane, reason = routine_forms[message]
            add(record, "routine_skip", lane=lane, target_id="", reason=reason, raw_reason=message)
            continue
        match = re.search(r"Considering (mention|hot_post_reply) id=(\d+) author_id=([^\s]+) text=(.*)$", message, re.S)
        if match:
            lane = normalise_lane(match.group(1))
            target = match.group(2)
            pending[lane] = {"target_id": target, "author_id": match.group(3), "incoming_text": decode_literal(match.group(4))}
            add(record, "legacy_candidate_considered", lane=lane, **pending[lane])
            continue
        match = re.search(r"Generated reply to mention (\d+): (.*)$", message, re.S)
        if match:
            target = match.group(1)
            matches = [(lane, item) for lane, item in pending.items() if item.get("target_id") == target and lane in {"mention", "hot-post"}]
            lane, item = matches[-1] if matches else ("mention", {"target_id": target})
            item["actual_reply_text"] = decode_literal(match.group(2))
            pending[lane] = item
            add(record, "legacy_reply_generated", lane=lane, **item)
            continue
        match = re.search(r"Recorded and cached own auto-reply id=(\d+)", message)
        if match:
            active = [(lane, item) for lane, item in pending.items() if lane in {"mention", "hot-post"} and item.get("actual_reply_text")]
            if len(active) == 1:
                active[0][1]["reply_post_id"] = match.group(1)
            else:
                unmatched.append({"event_kind": "legacy_reply_post_id", "reason": "no unique explicit pending target", "record_id": record["record_id"], "timestamp": record["timestamp"]})
            continue
        if message == "Reply posted successfully":
            active = [(lane, item) for lane, item in pending.items() if lane in {"mention", "hot-post"} and item.get("actual_reply_text")]
            if len(active) == 1:
                lane, item = active[0]
                add(record, "legacy_reply_posted", lane=lane, **item)
                pending.pop(lane, None)
            else:
                unmatched.append({"event_kind": "legacy_reply_posted", "reason": "success line has no unique explicit pending target", "record_id": record["record_id"], "timestamp": record["timestamp"]})
            continue
        match = re.search(r"No usable reply generated for (mention|hot_post_reply) (\d+)", message)
        if match:
            lane = normalise_lane(match.group(1))
            target = match.group(2)
            item = pending.pop(lane, {"target_id": target})
            add(record, "legacy_editorial_no_reply", lane=lane, no_reply_reason="no_usable_reply_generated", **item)
            continue
        match = re.search(r"Skipping (mention|hot_post_reply) (\d+): (.*)$", message, re.S)
        if match:
            lane = normalise_lane(match.group(1))
            target = match.group(2)
            item = pending.pop(lane, {"target_id": target})
            add(record, "legacy_candidate_skipped", lane=lane, reason=match.group(3).strip(), **item)
            continue
        match = re.search(r"Considering quote tweet id=(\d+) author_id=([^\s]+) original_post_id=(\d+) text=(.*)$", message, re.S)
        if match:
            item = {
                "target_id": match.group(1),
                "author_id": match.group(2),
                "quoted_post_id": match.group(3),
                "incoming_text": decode_literal(match.group(4)),
            }
            pending["quote-tweet"] = item
            add(record, "legacy_candidate_considered", lane="quote-tweet", **item)
            continue
        match = re.search(r"Generated reply to quote tweet (\d+): (.*)$", message, re.S)
        if match:
            item = pending.get("quote-tweet")
            if not item or item.get("target_id") != match.group(1):
                unmatched.append(
                    {
                        "event_kind": "legacy_quote_reply_generated",
                        "message_class": "legacy_reply_target_mismatch",
                        "reason": "generated quote-tweet reply has no pending candidate with the same target ID",
                        "record_id": record["record_id"],
                        "target_id": match.group(1),
                        "timestamp": record["timestamp"],
                    }
                )
                continue
            item["actual_reply_text"] = decode_literal(match.group(2))
            pending["quote-tweet"] = item
            add(record, "legacy_reply_generated", lane="quote-tweet", **item)
            continue
        match = re.search(r"Recorded and cached own quote-tweet auto-reply id=(\d+)", message)
        if match and pending.get("quote-tweet"):
            pending["quote-tweet"]["reply_post_id"] = match.group(1)
            continue
        if message == "Quote-tweet reply posted successfully":
            item = pending.pop("quote-tweet", None)
            if item and item.get("actual_reply_text"):
                add(record, "legacy_reply_posted", lane="quote-tweet", **item)
            else:
                unmatched.append({"event_kind": "legacy_quote_reply_posted", "reason": "success line has no explicit pending target and reply", "record_id": record["record_id"], "timestamp": record["timestamp"]})
            continue
        match = re.search(r"No usable reply generated for quote tweet (\d+)", message)
        if match:
            item = pending.pop("quote-tweet", {"target_id": match.group(1)})
            add(record, "legacy_editorial_no_reply", lane="quote-tweet", no_reply_reason="no_usable_reply_generated", **item)
            continue
        match = re.search(r"Wrote confirmed reply receipt pending local reconciliation(?: source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+))?", message)
        if match and match.group(2):
            add(record, "confirmed_reply_receipt_written", lane=normalise_lane(match.group(1)), target_id=match.group(2), reply_post_id=match.group(3))
            continue
        match = re.search(r"Wrote conversational reply sending receipt source=([^\s]+) target_id=([^\s]+)", message)
        if match:
            add(record, "reply_sending_receipt_written", lane=normalise_lane(match.group(1)), target_id=match.group(2))
            continue
        match = re.search(r"Promoted conversational reply receipt to confirmed source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+)", message)
        if match:
            add(record, "confirmed_reply_receipt_promoted", lane=normalise_lane(match.group(1)), target_id=match.group(2), reply_post_id=match.group(3))
            continue
        match = re.search(r"Removed conversational reply sending receipt disposition=([^\s]+) source=([^\s]+) target_id=([^\s]+)", message)
        if match:
            add(record, "reply_sending_receipt_removed", lane=normalise_lane(match.group(2)), target_id=match.group(3), disposition=match.group(1), reason=match.group(1))
            continue
        match = re.search(r"Removed conversational reply sending receipt after definite non-success source=([^\s]+) target_id=([^\s]+)", message)
        if match:
            add(record, "reply_sending_receipt_removed", lane=normalise_lane(match.group(1)), target_id=match.group(2), disposition="definite_non_success", reason="definite_non_success")
            continue
        match = re.search(r"Removed conversational reply sending receipt after confirmed identity was preserved in canonical state source=([^\s]+) target_id=([^\s]+)", message)
        if match:
            add(record, "confirmed_reply_state_fallback", lane=normalise_lane(match.group(1)), target_id=match.group(2), disposition="confirmed_state_fallback")
            continue
        match = re.search(r"Removed reconciled confirmed-reply receipt(?: source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+))?", message)
        if match and match.group(2):
            add(record, "confirmed_reply_receipt_removed", lane=normalise_lane(match.group(1)), target_id=match.group(2), reply_post_id=match.group(3))
            continue
        match = re.search(r"Reconciling confirmed reply receipt(?: source=([^\s]+))? target_id=([^\s]+) reply_post_id=([^\s]+)", message)
        if match:
            add(record, "confirmed_reply_receipt_reconciled", lane=normalise_lane(match.group(1)), target_id=match.group(2), reply_post_id=match.group(3))
            continue
        if is_reply_related_legacy_record(record):
            unmatched.append(
                {
                    "event_kind": "legacy_reply_record",
                    "message_class": "unrecognised_legacy_reply_record",
                    "reason": "legacy reply-related record form is not supported",
                    "record_id": record["record_id"],
                    "timestamp": record["timestamp"],
                }
            )
    return events, unmatched


def normalise_routine_reason(reason: Any) -> str | None:
    text = " ".join(str(reason or "").strip().lower().replace("-", "_").split())
    if not text:
        return None
    if "spacing" in text or "minimum interval" in text or "between replies" in text:
        return "spacing"
    if "cap" in text or "quota" in text or "maximum" in text and "daily" in text:
        return "cap"
    if "cooldown" in text or "rate limit" in text:
        return "cooldown"
    if "already replied" in text or "already_replied" in text or "already handled" in text:
        return "already_replied"
    if "own account" in text or "own_account" in text or "self authored" in text:
        return "own_account"
    if "already seen" in text or "already_seen" in text or "dry_run_already_seen" in text:
        return "already_seen"
    if "lane_not_due" in text or "lane not due" in text or "check not due" in text:
        return "lane_not_due"
    return None


def normalise_reason(reason: Any) -> str:
    return "_".join(str(reason or "").strip().lower().replace("-", "_").split())


def skip_reason_taxonomy(reason: Any) -> tuple[str | None, str | None]:
    """Return an explicit terminal class and optional routine label."""
    routine = normalise_routine_reason(reason)
    if routine:
        return None, routine
    value = normalise_reason(reason)
    if value in LOCAL_REJECTION_REASONS or value.startswith("already_evaluated_reply_not_permitted"):
        return "deterministic_rejection", None
    if value in EDITORIAL_SKIP_REASONS or value.startswith("already_evaluated_no_reply"):
        return "editorial_no_reply", None
    if value in OPERATIONAL_SKIP_REASONS:
        return "operational_failure", None
    return None, None


def parse_timestamp_sort(value: str) -> tuple[int, str]:
    return (0, value) if value else (1, "")


def ast_versions(data: bytes) -> tuple[dict[str, str], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    try:
        tree = ast.parse(data.decode("utf-8", "surrogateescape"))
    except (SyntaxError, UnicodeError) as exc:
        return {}, [warning("python_ast_parse_failed", detail=str(exc))]
    values: dict[str, str] = {}
    for node in tree.body:
        name: str | None = None
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        if name in VERSION_CONSTANTS and isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            values[name] = value_node.value
    return dict(sorted(values.items())), warnings


def safe_git_read(path: Path, project: Path) -> str | None:
    try:
        return safe_read_bytes(path, project).decode("ascii", "strict").strip()
    except (ExtractionError, UnicodeError):
        return None


def resolve_git_head(project: Path) -> str | None:
    dot_git = project / ".git"
    if not dot_git.exists() and not dot_git.is_symlink():
        return None
    try:
        resolved = dot_git.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not is_within(resolved, project):
        return None
    git_dir = resolved
    if resolved.is_file():
        pointer = safe_git_read(dot_git, project)
        if not pointer or not pointer.startswith("gitdir: "):
            return None
        candidate = (dot_git.parent / pointer[8:].strip()).resolve(strict=False)
        if not is_within(candidate, project) or not candidate.is_dir():
            return None
        git_dir = candidate
    head = safe_git_read(git_dir / "HEAD", project)
    if not head:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", head):
        return head.lower()
    if not head.startswith("ref: "):
        return None
    ref = head[5:].strip()
    if not re.fullmatch(r"refs/[A-Za-z0-9._/-]+", ref) or ".." in Path(ref).parts:
        return None
    loose = safe_git_read(git_dir / ref, project)
    if loose and re.fullmatch(r"[0-9a-fA-F]{40,64}", loose):
        return loose.lower()
    packed = safe_git_read(git_dir / "packed-refs", project)
    if packed:
        for line in packed.splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-fA-F]{40,64}", parts[0]):
                return parts[0].lower()
    return None


def epoch_timestamp(value: Any) -> str:
    try:
        epoch = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return ""


def supplemental_json_evidence(
    name: str,
    data: bytes,
    source_identity: str,
    source_sha: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        document = json.loads(data.decode("utf-8", "surrogateescape"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        return [], [warning("json_parse_failed", detail=str(exc))]

    rows: list[dict[str, Any]] = []
    if name == "bot_state.json" and isinstance(document, dict):
        for key in ("ai_reply_history", "reply_strategy_history"):
            value = document.get(key)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    elif name == "confirmed_reply_receipt.json" and isinstance(document, dict):
        rows = [document]
    for index, row in enumerate(rows):
        target = target_from_event(row)
        if not target:
            continue
        lane = normalise_lane(row.get("lane") or row.get("candidate_source") or row.get("source"))
        if lane not in CONVERSATIONAL_LANES:
            continue
        epoch_value = row.get("confirmation_epoch") or row.get("reply_epoch") or row.get("completed_epoch")
        iso_value = row.get("creation_time") or row.get("confirmed_at") or row.get("created_at") or ""
        original_timestamp = str(epoch_value if epoch_value not in (None, "") else iso_value)
        timestamp = epoch_timestamp(epoch_value) if epoch_value not in (None, "") else utc_timestamp(iso_value)
        status = str(row.get("status") or row.get("lifecycle_state") or "")
        actual_reply = row.get("proposed_reply") or row.get("reply_text")
        fields = dict(row)
        fields.update(
            {
                "actual_reply_text": actual_reply,
                "lane": lane,
                "supplemental_source": source_identity,
                "supplemental_source_sha256": source_sha,
                "target_id": target,
            }
        )
        event_hash = sha256_bytes(json_text(row).encode("ascii"))
        events.append(
            {
                "kind": "supplemental_reply_evidence",
                "original_timestamp_text": original_timestamp,
                "record_id": "",
                "supplemental_evidence_id": stable_id("evidence", event_hash),
                "supplemental_occurrence_id": stable_id("supplemental-occurrence", source_identity, index, event_hash),
                "timestamp": timestamp,
                "status": status,
                **fields,
            }
        )
    return events, warnings


def event_outcome(event: dict[str, Any]) -> str | None:
    kind = str(event.get("kind") or "")
    status = str(event.get("status") or "").lower()
    reason = normalise_reason(
        event.get("reason") or event.get("no_reply_reason") or event.get("failure_reason")
    )
    if kind in {
        "mention_reply_posted",
        "hot_post_reply_posted",
        "quote_tweet_reply_posted",
        "reply_posted",
        "legacy_reply_posted",
        "confirmed_reply_receipt_written",
        "confirmed_reply_receipt_promoted",
        "confirmed_reply_receipt_removed",
        "confirmed_reply_receipt_reconciled",
        "confirmed_reply_state_fallback",
    }:
        return "posted"
    if kind in {"reply_strategy_outcome", "ai_reply_pipeline_outcome"} or (
        "hot_post" in kind and "outcome" in kind
    ):
        if status in {"confirmed", "posted", "completed", "already_completed"}:
            return "posted"
        if status.startswith("posting_failed") or status in {"failed", "operational_failure", "error"}:
            return "operational_failure"
        if status in {"no_reply", "declined", "editorial_no_reply"}:
            return "editorial_no_reply"
    if kind in {"reply_strategy_failure", "ai_reply_pipeline_failure", "reply_evidence_unavailable"}:
        return "operational_failure"
    if kind == "reply_sending_receipt_removed" and str(event.get("disposition") or "") == "definite_non_success":
        return "operational_failure"
    if kind in {"mention_grok_skip", "hot_post_reply_grok_skip", "legacy_editorial_no_reply"}:
        return "editorial_no_reply"
    if kind == "reply_strategy_decision" or kind == "ai_reply_pipeline_decision":
        if reason in LOCAL_REJECTION_REASONS:
            return "deterministic_rejection"
        if reason in NON_TERMINAL_REASONS or status == "disabled":
            return None
        if str(event.get("mode") or "").lower() == "no_reply" or status == "no_reply":
            return "editorial_no_reply"
    if kind in {"reply_target_terminal", "reply_strategy_local_rejection", "reply_strategy_rejection"}:
        return "deterministic_rejection"
    if kind in {"candidate_skipped", "legacy_candidate_skipped"}:
        outcome, _routine = skip_reason_taxonomy(reason)
        return outcome
    if kind == "supplemental_reply_evidence":
        if event.get("reply_post_id") and event.get("actual_reply_text"):
            return "posted"
        if status in {"completed", "confirmed", "posted", "already_completed"}:
            return "posted"
        if status in {"failed", "confirmed_failed", "error"}:
            return "operational_failure"
    return None


def candidate_field_values(events: list[dict[str, Any]], keys: tuple[str, ...]) -> list[tuple[Any, str]]:
    values: list[tuple[Any, str]] = []
    seen: set[str] = set()
    for event in events:
        value: Any = None
        for key in keys:
            if key in event and event[key] not in (None, "", [], {}):
                value = event[key]
                break
        if value in (None, "", [], {}):
            continue
        encoded = json_text(value)
        if encoded in seen:
            continue
        seen.add(encoded)
        values.append((value, str(event.get("record_id") or event.get("supplemental_evidence_id") or "")))
    return values


def reconstruct_candidates(
    evidence: list[dict[str, Any]],
    version_rows: list[dict[str, Any]],
    record_snapshots: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    routine_map: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unmatched: list[dict[str, Any]] = []
    for event in evidence:
        lane = normalise_lane(event.get("lane"))
        if lane not in CONVERSATIONAL_LANES and not (
            event.get("kind") == "routine_skip" and lane == "unavailable"
        ):
            continue
        target = str(event.get("target_id") or "")
        routine_reason = None
        if event.get("kind") in {"candidate_skipped", "legacy_candidate_skipped", "routine_skip"}:
            routine_reason = normalise_routine_reason(event.get("reason"))
        if routine_reason:
            source_evidence_id = str(
                event.get("record_id")
                or event.get("supplemental_evidence_id")
                or stable_id("routine-source", json_text(event))
            )
            key = (str(event.get("timestamp") or ""), lane, target, routine_reason, source_evidence_id)
            row = routine_map.setdefault(
                key,
                {
                    "lane": lane,
                    "raw_reasons": [],
                    "reason": routine_reason,
                    "routine_skip_id": stable_id("routine-skip", *key),
                    "source_record_ids": [],
                    "target_id": target or None,
                    "timestamp": event.get("timestamp") or "",
                },
            )
            raw_reason = str(event.get("reason") or "")
            if raw_reason and raw_reason not in row["raw_reasons"]:
                row["raw_reasons"].append(raw_reason)
            if event.get("record_id") and event["record_id"] not in row["source_record_ids"]:
                row["source_record_ids"].append(event["record_id"])
            continue
        if not target:
            unmatched.append(
                {
                    "event_kind": event.get("kind"),
                    "reason": "reply evidence lacks explicit target identity",
                    "record_id": event.get("record_id") or "",
                    "timestamp": event.get("timestamp") or "",
                }
            )
            continue
        grouped[(lane, target)].append(event)

    versions_by_name = {str(row["snapshot_name"]): row for row in version_rows if row.get("source_type") == "snapshot"}
    candidates: list[dict[str, Any]] = []
    ambiguity_count = 0
    field_map = {
        "author_id": ("author_id",),
        "thread_id": ("thread_id", "conversation_id"),
        "incoming_text": ("incoming_text", "incoming_contribution"),
        "quoted_post_id": ("quoted_post_id", "original_post_id"),
        "quoted_post_text": ("quoted_post_text", "original_post_text"),
        "bounded_parent_context": ("bounded_parent_context", "parent_context"),
        "actual_reply_text": ("actual_reply_text", "reply_text", "reply", "proposed_reply"),
        "reply_post_id": ("reply_post_id",),
        "no_reply_reason": ("no_reply_reason",),
        "deterministic_rejection_reason": (
            "deterministic_rejection_reason",
            "reason",
            "no_reply_reason",
        ),
        "mode": ("mode", "proposer_mode"),
        "tone": ("tone", "humour_tone"),
        "reviewer_verdict": ("reviewer_verdict",),
        "factual_claim_count": ("factual_claim_count",),
        "model_call_count": ("model_call_count",),
        "revision_count": ("revision_count",),
    }
    for (lane, target), events in sorted(grouped.items()):
        events.sort(key=lambda item: (parse_timestamp_sort(str(item.get("timestamp") or "")), str(item.get("record_id") or item.get("supplemental_evidence_id") or ""), str(item.get("kind") or "")))
        source_record_ids = sorted({str(item.get("record_id")) for item in events if item.get("record_id")})
        supplemental_ids = sorted({str(item.get("supplemental_evidence_id")) for item in events if item.get("supplemental_evidence_id")})
        supplemental_occurrences = sorted(
            {
                json_text(occurrence): occurrence
                for item in events
                for occurrence in item.get("supplemental_occurrences", [])
            }.values(),
            key=lambda item: (
                str(item.get("supplemental_source") or ""),
                str(item.get("supplemental_occurrence_id") or ""),
            ),
        )
        timestamp_evidence = sorted(
            {
                json_text(row): row
                for item in events
                for row in (
                    {
                        "evidence_id": str(
                            item.get("record_id")
                            or item.get("supplemental_evidence_id")
                            or ""
                        ),
                        "original_timestamp_text": item.get("original_timestamp_text"),
                        "timestamp": item.get("timestamp") or None,
                    },
                )
                if row["timestamp"] or row["original_timestamp_text"]
            }.values(),
            key=lambda row: (
                str(row.get("timestamp") or ""),
                str(row.get("evidence_id") or ""),
                str(row.get("original_timestamp_text") or ""),
            ),
        )
        conflicts: list[dict[str, Any]] = []
        for item in events:
            for conflict in item.get("derivation_conflicts", []):
                conflicts.append(
                    {
                        "field": f"legacy_derivation:{conflict['field']}",
                        "values": [
                            conflict["retained_value"],
                            conflict["alternate_value"],
                        ],
                        "evidence_ids": [str(item.get("record_id") or "")],
                    }
                )
        resolved: dict[str, Any] = {}
        for output_field, keys in field_map.items():
            values = candidate_field_values(events, keys)
            if output_field == "deterministic_rejection_reason":
                values = [item for item in values if event_outcome(next((event for event in events if (event.get("record_id") or event.get("supplemental_evidence_id")) == item[1]), {})) == "deterministic_rejection"]
            if len(values) == 1:
                resolved[output_field] = values[0][0]
            elif len(values) > 1:
                resolved[output_field] = None
                conflicts.append(
                    {
                        "field": output_field,
                        "values": [value for value, _source in values],
                        "evidence_ids": [source for _value, source in values],
                    }
                )
            else:
                resolved[output_field] = None

        outcome_events = [(event_outcome(item), item) for item in events]
        terminal = [(outcome, item) for outcome, item in outcome_events if outcome]
        outcome_classes = sorted({outcome for outcome, _item in terminal if outcome})
        if "posted" in outcome_classes:
            # A later successful retry supersedes an earlier operational failure.
            contradictory = [value for value in outcome_classes if value not in {"posted", "operational_failure"}]
            outcome = "posted"
            if contradictory:
                conflicts.append({"field": "outcome", "values": outcome_classes, "evidence_ids": sorted({str(item.get("record_id") or item.get("supplemental_evidence_id") or "") for _value, item in terminal})})
        elif "deterministic_rejection" in outcome_classes:
            # Explicit local terminal reasons cannot be re-labelled editorial
            # merely because a generic legacy no-usable-reply line also exists.
            outcome = "deterministic_rejection"
            if len(outcome_classes) > 1:
                conflicts.append({"field": "outcome", "values": outcome_classes, "evidence_ids": sorted({str(item.get("record_id") or item.get("supplemental_evidence_id") or "") for _value, item in terminal})})
        elif len(outcome_classes) == 1:
            outcome = outcome_classes[0]
        elif len(outcome_classes) > 1:
            outcome = outcome_classes[-1]
            conflicts.append({"field": "outcome", "values": outcome_classes, "evidence_ids": sorted({str(item.get("record_id") or item.get("supplemental_evidence_id") or "") for _value, item in terminal})})
        else:
            outcome = "unresolved"

        explicit_versions = candidate_field_values(events, ("strategy_version",))
        prompt_keys = (
            "proposer_prompt_version",
            "reviewer_prompt_version",
            "no_reply_review_prompt_version",
            "claim_auditor_prompt_version",
        )
        prompt_values = {key: candidate_field_values(events, (key,)) for key in prompt_keys}
        version_evidence: dict[str, Any]
        strategy_version: str | None
        version_confidence: str
        if explicit_versions:
            distinct = [value for value, _source in explicit_versions]
            if len(distinct) == 1:
                strategy_version = str(distinct[0])
                version_confidence = "exact"
            else:
                strategy_version = None
                version_confidence = "exact"
                conflicts.append({"field": "strategy_version", "values": distinct, "evidence_ids": [source for _value, source in explicit_versions]})
            version_evidence = {
                "basis": "explicit log or persisted candidate versions",
                "confidence": "exact",
                "prompt_versions": {key: values[0][0] if len(values) == 1 else [value for value, _source in values] for key, values in prompt_values.items() if values},
                "strategy_versions": distinct,
            }
        else:
            containing = sorted(
                {snapshot for record_id in source_record_ids for snapshot in record_snapshots.get(record_id, set())},
                key=snapshot_sort_key,
            )
            earliest = containing[0] if containing else None
            version_row = versions_by_name.get(earliest or "")
            constants = version_row.get("constants", {}) if version_row else {}
            strategy_version = constants.get("STRATEGY_VERSION")
            version_confidence = "approximate" if version_row else "unavailable"
            version_evidence = {
                "basis": "earliest selected snapshot containing candidate" if version_row else "unavailable",
                "confidence": version_confidence,
                "snapshot": earliest,
                "snapshot_constants": constants,
            }

        timestamps = sorted(str(item.get("timestamp") or "") for item in events if item.get("timestamp"))
        terminal_timestamps = sorted(str(item.get("timestamp") or "") for _outcome, item in terminal if item.get("timestamp"))
        required_complete = bool(target and lane != "unavailable" and outcome != "unresolved" and resolved.get("incoming_text"))
        if outcome == "posted":
            required_complete = required_complete and bool(resolved.get("actual_reply_text") and resolved.get("reply_post_id"))
        elif outcome == "editorial_no_reply":
            required_complete = required_complete and bool(resolved.get("no_reply_reason") or any(str(item.get("reason") or "") for item in events))
        elif outcome == "deterministic_rejection":
            required_complete = required_complete and bool(resolved.get("deterministic_rejection_reason"))
        if conflicts:
            status = "ambiguous"
            ambiguity_count += 1
        else:
            status = "complete" if required_complete else "partial"
        notes: list[str] = []
        raw_reasons = sorted(
            {
                str(value)
                for item in events
                for value in (
                    item.get("reason"),
                    item.get("no_reply_reason"),
                    item.get("failure_reason"),
                )
                if value not in (None, "")
            }
        )
        unknown_skip_reasons = sorted(
            {
                str(item.get("reason"))
                for item in events
                if item.get("kind") in {"candidate_skipped", "legacy_candidate_skipped"}
                and item.get("reason")
                and skip_reason_taxonomy(item.get("reason")) == (None, None)
            }
        )
        if not resolved.get("incoming_text"):
            notes.append("incoming contribution is absent from available evidence")
        if outcome == "posted" and not resolved.get("actual_reply_text"):
            notes.append("posted disposition is present but reply text is absent")
        if outcome == "unresolved":
            notes.append("no terminal disposition is present in selected evidence")
        if unknown_skip_reasons:
            notes.append(
                "unknown candidate skip reason retained without inferring a terminal disposition: "
                + ", ".join(unknown_skip_reasons)
            )
        if any(normalise_reason(reason) in NON_TERMINAL_REASONS for reason in raw_reasons):
            notes.append("strategy-disabled execution is retained as non-terminal evidence")
        if version_confidence == "approximate":
            notes.append("version is inferred from the earliest selected snapshot containing the candidate; no exact deployment boundary is claimed")
        candidate = {
            "actual_reply_text": resolved.get("actual_reply_text"),
            "author_id": resolved.get("author_id"),
            "bounded_parent_context": resolved.get("bounded_parent_context"),
            "candidate_id": stable_id("candidate", lane, target),
            "conflict_evidence": conflicts,
            "deterministic_rejection_reason": resolved.get("deterministic_rejection_reason") if outcome == "deterministic_rejection" else None,
            "factual_claim_count": resolved.get("factual_claim_count"),
            "first_timestamp": timestamps[0] if timestamps else None,
            "incoming_text": resolved.get("incoming_text"),
            "lane": lane,
            "mode": resolved.get("mode"),
            "model_call_count": resolved.get("model_call_count"),
            "no_reply_reason": resolved.get("no_reply_reason") or (next((str(item.get("reason")) for item in events if event_outcome(item) == "editorial_no_reply" and item.get("reason")), None) if outcome == "editorial_no_reply" else None),
            "outcome": outcome,
            "prompt_version_evidence": version_evidence,
            "quoted_post_id": resolved.get("quoted_post_id"),
            "quoted_post_text": resolved.get("quoted_post_text"),
            "raw_reasons": raw_reasons,
            "reconstruction_notes": notes,
            "reconstruction_status": status,
            "reply_post_id": resolved.get("reply_post_id"),
            "reviewer_verdict": resolved.get("reviewer_verdict"),
            "revision_count": resolved.get("revision_count"),
            "source_record_ids": source_record_ids,
            "strategy_version": strategy_version,
            "supplemental_evidence_ids": supplemental_ids,
            "supplemental_occurrences": supplemental_occurrences,
            "target_id": target,
            "terminal_timestamp": terminal_timestamps[-1] if terminal_timestamps else None,
            "thread_id": resolved.get("thread_id"),
            "timestamp_evidence": timestamp_evidence,
            "tone": resolved.get("tone"),
            "version_confidence": version_confidence,
        }
        candidates.append(candidate)
    routine = sorted(routine_map.values(), key=lambda row: (row["timestamp"], row["lane"], str(row["target_id"] or ""), row["reason"], row["routine_skip_id"]))
    return candidates, routine, unmatched, ambiguity_count


def tokens(text: Any) -> list[str]:
    return WORD_RE.findall(str(text or "").lower())


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS]


def connected_similarity_groups(texts: list[str], threshold: float) -> list[list[int]]:
    parent = list(range(len(texts)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for left in range(len(texts)):
        for right in range(left + 1, len(texts)):
            if SequenceMatcher(None, texts[left], texts[right], autojunk=False).ratio() >= threshold:
                union(left, right)
    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(len(texts)):
        grouped[find(index)].append(index)
    return sorted((group for group in grouped.values() if len(group) > 1), key=lambda group: (group[0], group))


def frequency_rows(counter: Counter[str], *, minimum: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    return [
        {"count": count, "text": value}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if count >= minimum
    ][:limit]


def distribution(values: Iterable[int], buckets: tuple[int, ...]) -> dict[str, int]:
    result: Counter[str] = Counter()
    for value in values:
        lower = 0
        placed = False
        for upper in buckets:
            if value <= upper:
                result[f"{lower}-{upper}"] += 1
                placed = True
                break
            lower = upper + 1
        if not placed:
            result[f"{lower}+"] += 1
    return dict(sorted(result.items()))


def quality_inventory(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    posted = [
        item
        for item in candidates
        if item.get("lane") in CONVERSATIONAL_LANES
        and item["outcome"] == "posted"
        and isinstance(item.get("actual_reply_text"), str)
        and item["actual_reply_text"]
    ]
    replies = [str(item["actual_reply_text"]) for item in posted]
    normalised = [" ".join(tokens(reply)) for reply in replies]
    exact: dict[str, list[int]] = defaultdict(list)
    normal: dict[str, list[int]] = defaultdict(list)
    for index, reply in enumerate(replies):
        exact[reply].append(index)
        normal[normalised[index]].append(index)

    def duplicate_groups(groups: dict[str, list[int]], rule: str) -> list[dict[str, Any]]:
        return [
            {"candidate_ids": [posted[index]["candidate_id"] for index in indices], "count": len(indices), "rule": rule, "text": value}
            for value, indices in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
            if len(indices) > 1
        ]

    near: dict[str, list[dict[str, Any]]] = {}
    for threshold in (0.90, 0.85):
        key = f"{threshold:.2f}"
        near[key] = [
            {
                "candidate_ids": [posted[index]["candidate_id"] for index in group],
                "count": len(group),
                "rule": f"connected component of pairwise SequenceMatcher ratios >= {threshold:.2f} over lowercase alphanumeric token strings",
            }
            for group in connected_similarity_groups(normalised, threshold)
        ]

    prefix_counts: Counter[str] = Counter()
    suffix_counts: Counter[str] = Counter()
    ngram_counts: Counter[str] = Counter()
    sentence_openings: Counter[str] = Counter()
    sentence_endings: Counter[str] = Counter()
    for reply in replies:
        words = tokens(reply)
        for size in range(2, 7):
            if len(words) >= size:
                prefix_counts[" ".join(words[:size])] += 1
                suffix_counts[" ".join(words[-size:])] += 1
                for start in range(0, len(words) - size + 1):
                    ngram_counts[" ".join(words[start : start + size])] += 1
        for sentence in [part.group(0).strip() for part in SENTENCE_RE.finditer(reply) if part.group(0).strip()]:
            sentence_words = tokens(sentence)
            if sentence_words:
                sentence_openings[" ".join(sentence_words[: min(3, len(sentence_words))])] += 1
                sentence_endings[" ".join(sentence_words[-min(3, len(sentence_words)) :])] += 1

    proxy_rows: list[dict[str, Any]] = []
    for candidate in posted:
        incoming = set(content_tokens(candidate.get("incoming_text")))
        reply_content = content_tokens(candidate.get("actual_reply_text"))
        overlap = sorted({token for token in reply_content if token in incoming})
        novel = sorted({token for token in reply_content if token not in incoming})
        proxy_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "incoming_content_token_count": len(incoming),
                "overlap_content_tokens": overlap,
                "overlap_count": len(overlap),
                "reply_content_token_count": len(set(reply_content)),
                "reply_content_token_overlap_ratio": round(len(overlap) / len(set(reply_content)), 6) if reply_content else None,
                "reply_content_tokens_not_in_contribution": novel,
            }
        )

    count_dimensions: dict[str, dict[str, int]] = {}
    for field, missing in (("lane", "unavailable"), ("strategy_version", "unavailable"), ("mode", "unavailable"), ("tone", "unavailable")):
        count_dimensions[field if field != "strategy_version" else "era"] = dict(sorted(Counter(str(item.get(field) or missing) for item in posted).items()))
    diagnostic = {
        phrase: sum(reply.lower().count(phrase) for reply in replies)
        for phrase in DIAGNOSTIC_PHRASES
    }
    formulaic: list[dict[str, Any]] = []
    for label, rows in (("prefix", prefix_counts), ("suffix", suffix_counts)):
        for phrase, count in sorted(rows.items(), key=lambda item: (-item[1], item[0])):
            if count < 2:
                continue
            matching = [posted[index]["candidate_id"] for index, text in enumerate(normalised) if (text.startswith(phrase) if label == "prefix" else text.endswith(phrase))]
            formulaic.append({"candidate_ids": matching, "count": count, "phrase": phrase, "rule": f"normalised reply has the same complete {len(phrase.split())}-word {label}; frequency >= 2", "type": f"repeated_{label}"})
    for group in near["0.85"]:
        formulaic.append({**group, "rule": "connected component of pairwise SequenceMatcher ratios >= 0.85 over lowercase alphanumeric token strings", "type": "near_duplicate_structure"})
    formulaic.sort(key=lambda row: (str(row["type"]), -int(row["count"]), str(row.get("phrase") or ""), row["candidate_ids"]))
    return {
        "added_value_proxy": {
            "description": "Lexical content-token overlap only; it is not semantic quality and does not label replies good or bad.",
            "rows": proxy_rows,
            "stop_words": sorted(STOP_WORDS),
            "token_rule": "lowercase ASCII alphanumeric words with internal apostrophes; documented stop words removed",
        },
        "candidate_formulaic_clusters": formulaic,
        "counts_by": count_dimensions,
        "diagnostic_phrase_frequency": diagnostic,
        "exact_duplicate_groups": duplicate_groups(exact, "byte-for-byte identical Unicode reply text"),
        "most_frequent_complete_replies": frequency_rows(Counter(replies), minimum=1, limit=50),
        "most_frequent_prefixes_2_to_6_words": frequency_rows(prefix_counts, minimum=2),
        "most_frequent_suffixes_2_to_6_words": frequency_rows(suffix_counts, minimum=2),
        "near_duplicate_groups": near,
        "normalised_duplicate_groups": duplicate_groups(normal, "identical lowercase alphanumeric token sequence"),
        "recurring_ngrams_2_to_6_words": frequency_rows(ngram_counts, minimum=2),
        "repeated_sentence_endings": frequency_rows(sentence_endings, minimum=2),
        "repeated_sentence_openings": frequency_rows(sentence_openings, minimum=2),
        "reply_length_distribution_characters": distribution((len(reply) for reply in replies), (40, 80, 120, 160, 240, 400)),
        "reply_length_distribution_words": distribution((len(tokens(reply)) for reply in replies), (5, 10, 20, 30, 50, 80)),
        "schema_version": SCHEMA_VERSION,
        "sentence_count_distribution": dict(sorted(Counter(str(len([part for part in SENTENCE_RE.findall(reply) if part.strip()])) for reply in replies).items())),
        "tool_version": TOOL_VERSION,
        "total_posted_candidates": sum(
            item["outcome"] == "posted" and item.get("lane") in CONVERSATIONAL_LANES
            for item in candidates
        ),
        "total_posted_replies_with_text": len(posted),
    }


def markdown_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        result.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in row) + " |")
    return result


def quality_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Deterministic reply quality inventory",
        "",
        "This inventory preserves candidate repetition. Only overlapping source-copy duplication was removed upstream. It makes no model calls and does not assign semantic quality.",
        "",
        f"Posted candidates: {inventory['total_posted_candidates']}; posted replies with recovered text: {inventory['total_posted_replies_with_text']}.",
        "",
        "## Repetition",
        "",
        f"Exact duplicate groups: {len(inventory['exact_duplicate_groups'])}; normalised duplicate groups: {len(inventory['normalised_duplicate_groups'])}; near-duplicate components at 0.90: {len(inventory['near_duplicate_groups']['0.90'])}; at 0.85: {len(inventory['near_duplicate_groups']['0.85'])}.",
        "",
        "Near-duplicate groups are connected components of pairwise `difflib.SequenceMatcher(autojunk=False)` ratios over lowercase alphanumeric token strings at the stated threshold.",
        "",
        "## Recurring forms",
        "",
    ]
    lines.extend(markdown_table(["Measure", "Count"], [
        ("Repeated 2-6 word prefixes", len(inventory["most_frequent_prefixes_2_to_6_words"])),
        ("Repeated 2-6 word suffixes", len(inventory["most_frequent_suffixes_2_to_6_words"])),
        ("Recurring 2-6 word n-grams", len(inventory["recurring_ngrams_2_to_6_words"])),
        ("Candidate formulaic clusters", len(inventory["candidate_formulaic_clusters"])),
    ]))
    lines.extend(["", "Diagnostic phrases are counts only and are not automatic quality verdicts.", ""])
    lines.extend(markdown_table(["Phrase", "Occurrences"], sorted(inventory["diagnostic_phrase_frequency"].items())))
    lines.extend([
        "",
        "## Added-value proxy",
        "",
        inventory["added_value_proxy"]["description"],
        "",
        f"Token rule: {inventory['added_value_proxy']['token_rule']}. Per-candidate overlap tokens and reply tokens absent from the incoming contribution are recorded in the JSON inventory.",
        "",
    ])
    return "\n".join(lines)


def gap_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    parsed: list[tuple[datetime, str]] = []
    for record in records:
        try:
            parsed.append((datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")), record["record_id"]))
        except ValueError:
            continue
    parsed.sort()
    gaps: list[dict[str, Any]] = []
    for (before, before_id), (after, after_id) in zip(parsed, parsed[1:]):
        seconds = int((after - before).total_seconds())
        if seconds >= 24 * 60 * 60:
            gaps.append({"after_record_id": after_id, "before_record_id": before_id, "end": after.isoformat().replace("+00:00", "Z"), "seconds": seconds, "start": before.isoformat().replace("+00:00", "Z")})
    return {
        "detected_temporal_gaps": gaps,
        "gap_rule": "adjacent canonical log-record timestamps differ by at least 86400 seconds",
        "note": "A temporal gap shows absent log evidence in the selected corpus; it does not prove the bot was active or inactive during the interval.",
        "schema_version": SCHEMA_VERSION,
    }


def coverage_markdown(
    discovered: list[str],
    selected: list[str],
    readable: list[str],
    skipped: list[dict[str, str]],
    source_rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    routine: list[dict[str, Any]],
    unmatched: list[dict[str, Any]],
    gaps: dict[str, Any],
) -> str:
    outcomes = Counter(item["outcome"] for item in candidates)
    lanes = Counter(item["lane"] for item in candidates)
    statuses = Counter(item["reconstruction_status"] for item in candidates)
    eras = Counter(str(item.get("strategy_version") or "unavailable") for item in candidates)
    # Source rows carry every warning with file provenance, including warnings
    # also retained on their canonical record. Count that authoritative list
    # once rather than double-counting canonical copies here.
    warnings = sum(len(row.get("parse_warnings", [])) for row in source_rows)
    missing_by_era: dict[str, Counter[str]] = defaultdict(Counter)
    for item in candidates:
        era = str(item.get("strategy_version") or "unavailable")
        if not item.get("incoming_text"):
            missing_by_era[era]["incoming_text"] += 1
        if item["outcome"] == "posted" and not item.get("actual_reply_text"):
            missing_by_era[era]["reply_text"] += 1
        if item["outcome"] == "unresolved":
            missing_by_era[era]["terminal_disposition"] += 1
    hashes = Counter(row["sha256"] for row in source_rows)
    duplicate_copies = sum(count - 1 for count in hashes.values())
    timestamps = sorted(record["timestamp"] for record in records)
    lines = [
        "# Reply history reconstruction coverage",
        "",
        "This is a forensic reconstruction from selected local evidence, not a claim of complete conversational history. Missing, rotated-away, malformed, or never-logged evidence cannot be recovered by this tool.",
        "",
        "## Sources",
        "",
        f"Snapshots discovered: {len(discovered)}; selected: {len(selected)}; readable: {len(readable)}; skipped: {len(skipped)}.",
        "",
        f"Source-file copies: {len(source_rows)}; unique byte versions: {len(hashes)}; duplicate byte-identical copies: {duplicate_copies}.",
        "",
        f"Raw parsed record occurrences: {sum(row.get('parsed_record_count', 0) for row in source_rows)}; canonical records: {len(records)}.",
        "",
        f"Coverage range: {timestamps[0] if timestamps else 'unavailable'} to {timestamps[-1] if timestamps else 'unavailable'}; detected >=24-hour temporal gaps: {len(gaps['detected_temporal_gaps'])}.",
        "",
        "Selected snapshots: " + (", ".join(selected) if selected else "none"),
        "",
        "Skipped snapshots: " + ("; ".join(f"{row['snapshot']} ({row['reason']})" for row in skipped) if skipped else "none"),
        "",
        "## Candidates",
        "",
    ]
    for title, counter in (("Outcome", outcomes), ("Lane", lanes), ("Reconstruction status", statuses), ("Version era", eras)):
        lines.extend([f"### {title}", ""])
        lines.extend(markdown_table([title, "Count"], sorted(counter.items())))
        lines.append("")
    lines.extend([
        "## Routine skips",
        "",
        f"Routine scheduling or eligibility skips are separate from candidates: {len(routine)}.",
        "",
    ])
    lines.extend(markdown_table(["Reason", "Count"], sorted(Counter(item["reason"] for item in routine).items())))
    lines.extend([
        "",
        "## Warnings, ambiguities, and missing evidence",
        "",
        f"Parser warnings: {warnings}; ambiguous candidates: {statuses.get('ambiguous', 0)}; unmatched relevant records: {len(unmatched)}.",
        "",
        "Missing evidence by version era:",
        "",
    ])
    missing_rows = [(era, field, count) for era in sorted(missing_by_era) for field, count in sorted(missing_by_era[era].items())]
    lines.extend(markdown_table(["Era", "Missing field", "Candidates"], missing_rows))
    lines.extend([
        "",
        "Warnings are retained with source provenance in `source_files.jsonl` and `unique_log_records.jsonl`; disagreements are retained in each candidate's `conflict_evidence`.",
        "",
        "The corpus must not be treated as complete where incoming text, reply text, terminal disposition, source intervals, or snapshot eras are absent. Snapshot overlap proves copies of observed bytes, not continuity between observations.",
        "",
    ])
    return "\n".join(lines)


def create_private_output(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        for row in rows:
            view = memoryview((json_text(row) + "\n").encode("ascii"))
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    snapshot_root = Path(args.snapshot_root).expanduser().resolve(strict=True)
    if not snapshot_root.is_dir():
        raise ExtractionError(f"snapshot root is not a directory: {snapshot_root}")
    relative = validate_relative_project_path(args.project_relative_path)
    discovered, available_projects, skipped = discover_snapshot_projects(snapshot_root, relative)
    selected = select_snapshots(discovered, available_projects, args.snapshot or [], args.max_snapshots)
    if args.list_snapshots:
        for name in sorted(available_projects, key=snapshot_sort_key):
            print(name)
        return {"listed": True}
    if not args.output:
        raise ExtractionError("--output is required unless --list-snapshots is used")
    live_project: Path | None = None
    if args.live_project:
        try:
            live_project = Path(args.live_project).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ExtractionError(f"live project is unavailable: {args.live_project}") from exc
        if not live_project.is_dir():
            raise ExtractionError(f"live project is not a directory: {live_project}")
    worktree = Path(__file__).resolve().parents[1]
    selected_projects = [available_projects[name] for name in selected]
    output = validate_output_path(Path(args.output), snapshot_root, selected_projects, live_project, worktree)
    created_at = normalise_created_at(args.created_at)

    projects: list[tuple[str, str, Path]] = [("snapshot", name, available_projects[name]) for name in selected]
    if live_project is not None:
        projects.append(("live", "live", live_project))
    source_rows: list[dict[str, Any]] = []
    canonical: dict[str, dict[str, Any]] = {}
    occurrence_rows: list[dict[str, Any]] = []
    record_snapshots: dict[str, set[str]] = defaultdict(set)
    structured: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    legacy_by_source_record: dict[tuple[str, str], dict[str, Any]] = {}
    legacy_unmatched: list[dict[str, Any]] = []
    supplemental_unique: dict[str, dict[str, Any]] = {}
    version_rows: list[dict[str, Any]] = []
    raw_record_occurrence_count = 0

    for source_type, identity, project in projects:
        stream_pair_counts: Counter[tuple[str, str]] = Counter()
        pair_paths: dict[tuple[str, str], set[str]] = defaultdict(set)
        legacy_pending: dict[str, dict[str, Any]] = {}
        constants: dict[str, str] = {}
        version_warnings: list[dict[str, Any]] = []
        hashes: dict[str, str | None] = {
            name: None for name in ("reply_strategy.py", "mrsMThatcher2.py", "bot_state.json")
        }
        for path in source_candidates(project):
            relative_path = path.relative_to(project).as_posix()
            key = f"{source_type}:{identity}:{relative_path}"
            data = safe_read_bytes(path, project)
            digest = sha256_bytes(data)
            byte_size = len(data)
            if path.name in hashes:
                hashes[path.name] = digest
            parse_warnings: list[dict[str, Any]] = []
            parsed: list[dict[str, Any]] = []
            if LOG_NAME_RE.fullmatch(path.name):
                parsed, parse_warnings = parse_log_records(data, key, stream_pair_counts)
                raw_record_occurrence_count += len(parsed)
                for sequence, record in enumerate(parsed, start=1):
                    record["source_file_sequence"] = sequence
                    record["source_identity"] = identity
                    record["source_key"] = key
                    record["source_path"] = relative_path
                    record["source_type"] = source_type
                    pair = (record["original_timestamp_text"], record["raw_record_sha256"])
                    prior_paths = pair_paths[pair]
                    if prior_paths and relative_path not in prior_paths:
                        record["parse_warnings"].append(
                            warning(
                                "ambiguous_identical_record_across_rotation_boundary",
                                other_source_paths=sorted(prior_paths),
                            )
                        )
                    prior_paths.add(relative_path)
                    parse_warnings.extend(
                        {
                            **item,
                            "record_id": record["record_id"],
                            "source_file_sequence": sequence,
                        }
                        for item in record["parse_warnings"]
                    )
                    record_id = record["record_id"]
                    occurrence_id = stable_id("occurrence", key, sequence, record_id)
                    occurrence_rows.append(
                        {
                            "occurrence_id": occurrence_id,
                            "pair_ordinal": record["pair_ordinal"],
                            "record_id": record_id,
                            "source_file_sequence": sequence,
                            "source_identity": identity,
                            "source_path": relative_path,
                            "source_type": source_type,
                        }
                    )
                    if source_type == "snapshot":
                        record_snapshots[record_id].add(identity)
                    if record_id not in canonical:
                        canonical[record_id] = {
                            field: record[field]
                            for field in (
                                "level",
                                "line",
                                "message",
                                "original_timestamp_text",
                                "pair_ordinal",
                                "parse_warnings",
                                "raw_record_sha256",
                                "raw_record_text",
                                "record_id",
                                "source_metadata",
                                "structured_event",
                                "timestamp",
                            )
                        }
                        canonical[record_id]["logger"] = None
                        canonical[record_id]["function"] = record["source_metadata"]
                        canonical[record_id]["source_occurrence_ids"] = []
                        found_structured, not_matched_structured = structured_evidence(
                            [canonical[record_id]]
                        )
                        structured.extend(found_structured)
                        unmatched.extend(not_matched_structured)
                    else:
                        known_warnings = {
                            json_text(item) for item in canonical[record_id]["parse_warnings"]
                        }
                        canonical[record_id]["parse_warnings"].extend(
                            item
                            for item in record["parse_warnings"]
                            if json_text(item) not in known_warnings
                        )
                    canonical[record_id]["source_occurrence_ids"].append(occurrence_id)
                found_legacy, not_matched_legacy = legacy_evidence(parsed, legacy_pending)
                for event in found_legacy:
                    event_identity = (str(event.get("kind") or ""), str(event.get("record_id") or ""))
                    retained = legacy_by_source_record.setdefault(event_identity, event)
                    if retained is not event:
                        conflicts = retained.setdefault("derivation_conflicts", [])
                        for field, value in event.items():
                            if value in (None, "", [], {}):
                                continue
                            if retained.get(field) in (None, "", [], {}):
                                retained[field] = value
                            elif retained[field] != value:
                                conflict = {
                                    "field": field,
                                    "retained_value": retained[field],
                                    "alternate_value": value,
                                }
                                if conflict not in conflicts:
                                    conflicts.append(conflict)
                legacy_unmatched.extend(not_matched_legacy)
            elif path.name in {"bot_state.json", *EXPLICIT_JSON_FILES}:
                evidence, json_warnings = supplemental_json_evidence(path.name, data, key, digest)
                parse_warnings.extend(json_warnings)
                for event in evidence:
                    occurrence = {
                        "supplemental_occurrence_id": event.pop("supplemental_occurrence_id"),
                        "supplemental_source": event.pop("supplemental_source"),
                        "supplemental_source_sha256": event.pop("supplemental_source_sha256"),
                    }
                    event_key = json_text(event)
                    retained = supplemental_unique.setdefault(
                        event_key, {**event, "supplemental_occurrences": []}
                    )
                    if occurrence not in retained["supplemental_occurrences"]:
                        retained["supplemental_occurrences"].append(occurrence)
            elif path.name == "reply_strategy.py":
                values, warnings_found = ast_versions(data)
                constants.update(values)
                version_warnings.extend(warnings_found)
                parse_warnings.extend(warnings_found)
            source_rows.append(
                {
                    "byte_size": byte_size,
                    "first_record_timestamp": parsed[0]["timestamp"] if parsed else None,
                    "is_log": bool(LOG_NAME_RE.fullmatch(path.name)),
                    "last_record_timestamp": parsed[-1]["timestamp"] if parsed else None,
                    "parsed_record_count": len(parsed),
                    "parse_warnings": parse_warnings,
                    "sha256": digest,
                    "snapshot_name": identity if source_type == "snapshot" else None,
                    "source_identity": identity,
                    "source_path": relative_path,
                    "source_type": source_type,
                }
            )
            # Drop the complete file bytes and parsed occurrence objects before
            # reading the next allow-listed source.
            del data
            del parsed
        inferred, method = inferred_snapshot_time(identity) if source_type == "snapshot" else (None, "live project is not a snapshot")
        version_rows.append(
            {
                "constants": constants,
                "git_head": resolve_git_head(project),
                "inferred_snapshot_ordering_timestamp": inferred,
                "ordering_timestamp_inference": method,
                "parse_warnings": version_warnings,
                "sha256": hashes,
                "snapshot_name": identity,
                "source_type": source_type,
            }
        )

    canonical_rows = sorted(canonical.values(), key=lambda row: (row["timestamp"], row["raw_record_sha256"], row["pair_ordinal"], row["record_id"]))
    for row in canonical_rows:
        row["source_occurrence_ids"].sort()
        row["occurrence_count"] = len(row["source_occurrence_ids"])
    occurrence_rows.sort(key=lambda row: (row["record_id"], row["source_type"], row["source_identity"], row["source_path"], row["source_file_sequence"]))
    source_rows.sort(key=lambda row: (row["source_type"], snapshot_sort_key(str(row["source_identity"])), source_sort_key(row["source_path"])))
    version_rows.sort(key=lambda row: (row["source_type"], snapshot_sort_key(str(row["snapshot_name"]))))

    for event in supplemental_unique.values():
        event["supplemental_occurrences"].sort(
            key=lambda row: (row["supplemental_source"], row["supplemental_occurrence_id"])
        )
    matched_legacy_record_ids = {
        str(event.get("record_id"))
        for event in legacy_by_source_record.values()
        if event.get("record_id")
    }
    legacy_unmatched = [
        row
        for row in legacy_unmatched
        if not row.get("record_id") or str(row["record_id"]) not in matched_legacy_record_ids
    ]
    candidates, routine, reconstruction_unmatched, ambiguity_count = reconstruct_candidates(
        [*structured, *legacy_by_source_record.values(), *supplemental_unique.values()], version_rows, record_snapshots
    )
    unmatched_rows = [*unmatched, *legacy_unmatched, *reconstruction_unmatched]
    unmatched_unique = {json_text(row): row for row in unmatched_rows}
    unmatched_rows = sorted(unmatched_unique.values(), key=lambda row: (str(row.get("timestamp") or ""), str(row.get("record_id") or ""), str(row.get("event_kind") or ""), str(row.get("reason") or "")))

    gaps = gap_report(canonical_rows)
    inventory = quality_inventory(candidates)
    coverage = coverage_markdown(
        discovered,
        selected,
        sorted(available_projects, key=snapshot_sort_key),
        [row for row in skipped if row["snapshot"] not in selected],
        source_rows,
        canonical_rows,
        candidates,
        routine,
        unmatched_rows,
        gaps,
    )
    source_warning_count = sum(len(row["parse_warnings"]) for row in source_rows)
    record_warning_count = sum(len(row["parse_warnings"]) for row in canonical_rows)
    counts = {
        "ambiguity_count": ambiguity_count,
        "candidate_count": len(candidates),
        "canonical_record_count": len(canonical_rows),
        "raw_record_occurrence_count": raw_record_occurrence_count,
        "record_warning_count": record_warning_count,
        "routine_skip_count": len(routine),
        "source_file_count": len(source_rows),
        "source_warning_count": source_warning_count,
        "unmatched_reply_record_count": len(unmatched_rows),
        "warning_total": source_warning_count,
    }
    manifest = {
        "arguments": {
            "created_at": args.created_at,
            "list_snapshots": bool(args.list_snapshots),
            "live_project": args.live_project,
            "max_snapshots": args.max_snapshots,
            "output": args.output,
            "project_relative_path": args.project_relative_path,
            "snapshot": list(args.snapshot or []),
            "snapshot_root": args.snapshot_root,
        },
        "counts": counts,
        "created_at": created_at,
        "live_project_included": live_project is not None,
        "output_file_inventory": list(OUTPUT_FILES),
        "schema_version": SCHEMA_VERSION,
        "selected_snapshots": selected,
        "source_roots": {
            "live_project": str(live_project) if live_project else None,
            "snapshot_root": str(snapshot_root),
        },
        "tool_version": TOOL_VERSION,
    }

    old_umask = os.umask(0o077)
    try:
        create_private_output(output)
        write_private(output / "run_manifest.json", json_text(manifest, pretty=True).encode("ascii"))
        write_jsonl(output / "source_files.jsonl", source_rows)
        write_jsonl(output / "snapshot_versions.jsonl", version_rows)
        write_jsonl(output / "unique_log_records.jsonl", canonical_rows)
        write_jsonl(output / "record_occurrences.jsonl", occurrence_rows)
        write_jsonl(output / "conversational_candidates.jsonl", candidates)
        write_jsonl(output / "routine_skips.jsonl", routine)
        write_jsonl(output / "unmatched_reply_records.jsonl", unmatched_rows)
        write_private(output / "reconstruction_gaps.json", json_text(gaps, pretty=True).encode("ascii"))
        write_private(output / "coverage_report.md", coverage.encode("utf-8"))
        write_private(output / "reply_quality_inventory.json", json_text(inventory, pretty=True).encode("ascii"))
        write_private(output / "reply_quality_inventory.md", quality_markdown(inventory).encode("utf-8"))
        checksum_lines: list[str] = []
        for filename in OUTPUT_FILES:
            if filename == "SHA256SUMS":
                continue
            checksum_lines.append(f"{sha256_bytes((output / filename).read_bytes())}  {filename}")
        write_private(output / "SHA256SUMS", ("\n".join(checksum_lines) + "\n").encode("ascii"))
    except Exception:
        # Do not remove a partially written corpus: preserving it is safer and
        # avoids a destructive operation. The next run will refuse it as non-empty.
        raise
    finally:
        os.umask(old_umask)
    return {"listed": False, "manifest": manifest, "output": str(output)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", required=True)
    parser.add_argument("--project-relative-path", default="etc/mrsMThatcher")
    parser.add_argument("--live-project")
    parser.add_argument("--output")
    parser.add_argument("--snapshot", action="append", default=[])
    parser.add_argument("--max-snapshots", type=int)
    parser.add_argument("--created-at")
    parser.add_argument("--list-snapshots", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        execute(args)
    except (ExtractionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
