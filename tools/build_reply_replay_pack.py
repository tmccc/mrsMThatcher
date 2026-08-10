#!/usr/bin/env python3
"""Build a deterministic, no-call conversational-reply replay pack."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Iterator, Mapping, Sequence


WORKTREE = Path(__file__).resolve().parents[1]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

import reply_strategy  # noqa: E402  (the worktree root is added deliberately)


SCHEMA_VERSION = 2
TOOL_VERSION = "reply-replay-pack-v2"
CASE_PACK_VERSION = "mrs-reply-evaluation-48-v1"
SOURCE_TOOL_VERSION = "reply-evaluation-pool-v1"
SOURCE_NORMALISATION_VERSION = "reply-candidate-normalisation-v1"
SOURCE_EXTRACTOR_COMMIT = "bdb6a5b18468bbda02f2c908f8a7699601ee5d18"
HISTORY_SCHEMA_VERSION = 2
HISTORY_TOOL_VERSION = "reply-history-reconstruction-v2"
HISTORY_EXTRACTOR_COMMIT_CONFIDENCE = "exact"
HISTORY_SELECTED_SNAPSHOT_COUNT = 32
QUOTE_CONTEXT_MAX_CHARS = 2_000
ACCOUNT_QUOTED_POST_TYPES = frozenset({"quote", "daily_meme"})

SELECTION_JSON = "mrsMThatcher-reply-evaluation-proposed-freeze-20260810.json"
SELECTION_CSV = "mrsMThatcher-reply-evaluation-proposed-freeze-20260810-selected-48.csv"
SELECTION_MD = "mrsMThatcher-reply-evaluation-proposed-freeze-20260810.md"
SELECTION_FILES = (SELECTION_JSON, SELECTION_CSV, SELECTION_MD)
SHORTLIST_FILES = (
    "run_manifest.json",
    "evaluation_eligible_candidates.jsonl",
    "normalised_candidates.jsonl",
)

FINAL_STRATA = (
    "formulaic_substantive_posted",
    "civil_challenge_or_disagreement",
    "genuine_social_courtesy",
    "factual_or_historical_question",
    "safe_wit_opportunity",
    "justified_safety_no_reply",
)
STRATUM_LABELS = {
    "formulaic_substantive_posted": "formulaic substantive case",
    "civil_challenge_or_disagreement": "civil challenge",
    "genuine_social_courtesy": "genuine courtesy case",
    "factual_or_historical_question": "factual/historical question",
    "safe_wit_opportunity": "safe-wit opportunity",
    "justified_safety_no_reply": "justified safety no-reply",
}
LANE_MAP = {
    "mention": "mention",
    "quote-tweet": "quote_tweet",
    "hot-post": "hot_post_reply",
}
FORBIDDEN_MODEL_FIELDS = (
    "historical_reply",
    "historical_outcome",
    "historical_mode",
    "historical_tone",
    "historical_reviewer_verdict",
    "historical_no_reply_reason",
    "final_stratum",
    "final_rank",
    "selection_rationale",
    "provisional_strata",
    "prompt_era_id",
    "normalisation_actions",
    "evaluation_eligibility",
)
OUTPUT_FILES = (
    "run_manifest.json",
    "source_verification.json",
    "frozen_cases.jsonl",
    "model_inputs.jsonl",
    "historical_baselines.jsonl",
    "recent_account_replies.jsonl",
    "calibration_cases.jsonl",
    "replay_plan.json",
    "case_pack_report.md",
    "leakage_audit.json",
    "quote_context_recovery.jsonl",
    "SHA256SUMS",
)
ISO_UTC_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
CHECKSUM_RE = re.compile(r"\A([0-9a-fA-F]{64}) [ *](.+)\Z")


class PackError(Exception):
    """A fail-closed validation error safe to report without candidate text."""

    def __init__(
        self,
        message: str,
        *,
        candidate_fields: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.candidate_fields = {
            candidate_id: sorted(set(fields))
            for candidate_id, fields in (candidate_fields or {}).items()
        }


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"invalid JSON input: {path.name}") from exc


def require_regular_file(path: Path) -> None:
    if not path.is_file():
        raise PackError(f"required input is missing: {path.name}")


def validate_created_at(value: str) -> str:
    if not ISO_UTC_RE.fullmatch(value):
        raise PackError("created_at must be an ISO UTC timestamp ending in Z")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PackError("created_at is not a valid UTC timestamp") from exc
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: object, field: str, candidate_id: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PackError(
            "candidate timestamp validation failed",
            candidate_fields={candidate_id: [field]},
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PackError(
            "candidate timestamp validation failed",
            candidate_fields={candidate_id: [field]},
        ) from exc
    if parsed.tzinfo is None:
        raise PackError(
            "candidate timestamp validation failed",
            candidate_fields={candidate_id: [field]},
        )
    return parsed.astimezone(timezone.utc)


def parse_checksum_file(path: Path) -> dict[str, str]:
    require_regular_file(path)
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PackError("unable to read SHA256SUMS") from exc
    for line_number, line in enumerate(lines, start=1):
        match = CHECKSUM_RE.fullmatch(line)
        if match is None:
            raise PackError(f"malformed SHA256SUMS entry at line {line_number}")
        digest, raw_name = match.groups()
        posix = PurePosixPath(raw_name)
        windows = PureWindowsPath(raw_name)
        if (
            not raw_name
            or posix.is_absolute()
            or windows.is_absolute()
            or ".." in posix.parts
            or ".." in windows.parts
        ):
            raise PackError(f"unsafe SHA256SUMS path at line {line_number}")
        if raw_name in entries:
            raise PackError(f"duplicate SHA256SUMS path: {raw_name}")
        entries[raw_name] = digest.lower()
    return entries


def verify_shortlist_checksums(shortlist: Path) -> tuple[dict[str, str], str]:
    checksum_path = shortlist / "SHA256SUMS"
    entries = parse_checksum_file(checksum_path)
    verified: dict[str, str] = {}
    for name in SHORTLIST_FILES:
        expected = entries.get(name)
        if expected is None:
            raise PackError(f"missing SHA256SUMS entry: {name}")
        path = shortlist / name
        require_regular_file(path)
        actual = sha256_file(path)
        if actual != expected:
            raise PackError(f"checksum mismatch: {name}")
        verified[name] = actual
    return verified, sha256_file(checksum_path)


def _selection_count(value: object, label: str) -> int:
    if type(value) is not int:
        raise PackError(f"selection {label} is invalid")
    return value


def read_selection(selection: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    for name in SELECTION_FILES:
        require_regular_file(selection / name)
    hashes = {name: sha256_file(selection / name) for name in SELECTION_FILES}
    document = load_json(selection / SELECTION_JSON)
    if not isinstance(document, dict):
        raise PackError("selection JSON root must be an object")
    counts = document.get("counts")
    rows = document.get("selected_cases")
    if (
        document.get("schema_version") != 1
        or document.get("selection_status") != "proposed_for_freeze"
        or not isinstance(counts, dict)
        or not isinstance(rows, list)
    ):
        raise PackError("selection JSON metadata is invalid")
    if (
        _selection_count(counts.get("selected_cases"), "selected_cases") != 48
        or _selection_count(counts.get("unique_selected_candidates"), "unique_selected_candidates") != 48
        or len(rows) != 48
    ):
        raise PackError("selection must contain exactly 48 unique selected cases")

    candidate_fields: dict[str, list[str]] = defaultdict(list)
    triples: list[tuple[str, str, int]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PackError(f"selection case {index + 1} is not an object")
        candidate_id = row.get("candidate_id")
        stratum = row.get("final_stratum")
        rank = row.get("final_rank")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise PackError(f"selection case {index + 1} has no candidate_id")
        if stratum not in FINAL_STRATA:
            candidate_fields[candidate_id].append("final_stratum")
        if type(rank) is not int:
            candidate_fields[candidate_id].append("final_rank")
        if stratum in FINAL_STRATA and type(rank) is int:
            triples.append((candidate_id, stratum, rank))
    if candidate_fields:
        raise PackError("selection case metadata is invalid", candidate_fields=candidate_fields)

    ids = [candidate_id for candidate_id, _, _ in triples]
    duplicates = sorted(candidate_id for candidate_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise PackError(
            "selected candidate occurs more than once",
            candidate_fields={candidate_id: ["candidate_id"] for candidate_id in duplicates},
        )
    by_stratum: dict[str, list[int]] = defaultdict(list)
    for _, stratum, rank in triples:
        by_stratum[stratum].append(rank)
    expected_counts = {stratum: 8 for stratum in FINAL_STRATA}
    actual_counts = {stratum: len(by_stratum[stratum]) for stratum in FINAL_STRATA}
    if actual_counts != expected_counts:
        raise PackError("selection must contain exactly eight cases in each final stratum")
    for stratum in FINAL_STRATA:
        if sorted(by_stratum[stratum]) != list(range(1, 9)):
            raise PackError(f"final ranks must be exactly 1 through 8 in {stratum}")
    stated_by_stratum = counts.get("selected_cases_per_stratum")
    if stated_by_stratum != expected_counts:
        raise PackError("selection per-stratum counts are invalid")

    csv_triples: list[tuple[str, str, int]] = []
    try:
        with (selection / SELECTION_CSV).open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            required = {"candidate_id", "final_stratum", "final_rank"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise PackError("selected-only CSV fields are invalid")
            for row_number, csv_row in enumerate(reader, start=2):
                try:
                    rank = int(csv_row["final_rank"])
                except (TypeError, ValueError) as exc:
                    raise PackError(f"selected-only CSV rank is invalid at line {row_number}") from exc
                csv_triples.append((csv_row["candidate_id"], csv_row["final_stratum"], rank))
    except (OSError, UnicodeError, csv.Error) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError("selected-only CSV is invalid") from exc
    if len(csv_triples) != 48 or Counter(csv_triples) != Counter(triples):
        raise PackError("selection JSON and selected-only CSV disagree")

    return rows, document, hashes


def verify_source_manifest(shortlist: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_json(shortlist / "run_manifest.json")
    if not isinstance(manifest, dict):
        raise PackError("shortlist manifest root must be an object")
    expected_top = {
        "/schema_version": (manifest.get("schema_version"), 1),
        "/tool_version": (manifest.get("tool_version"), SOURCE_TOOL_VERSION),
        "/normalisation_version": (
            manifest.get("normalisation_version"),
            SOURCE_NORMALISATION_VERSION,
        ),
        "/source_extractor_commit": (
            manifest.get("source_extractor_commit"),
            SOURCE_EXTRACTOR_COMMIT,
        ),
    }
    for path, (actual, expected) in expected_top.items():
        if actual != expected:
            raise PackError(f"shortlist manifest mismatch at {path}")
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise PackError("shortlist manifest counts are invalid")
    expected_counts = {
        "input_candidates": 589,
        "excluded_candidates": 3,
        "normalised_candidates": 586,
        "evaluation_eligible_candidates": 277,
    }
    for name, expected in expected_counts.items():
        if counts.get(name) != expected:
            raise PackError(f"shortlist manifest mismatch at /counts/{name}")
    tool_commit = manifest.get("running_tool_git_commit")
    if not isinstance(tool_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", tool_commit):
        raise PackError("shortlist manifest evaluation-pool tool commit is invalid")
    preserved_paths = {
        "/schema_version": manifest["schema_version"],
        "/tool_version": manifest["tool_version"],
        "/normalisation_version": manifest["normalisation_version"],
        "/source_extractor_commit": manifest["source_extractor_commit"],
        "/running_tool_git_commit": tool_commit,
        **{f"/counts/{name}": counts[name] for name in expected_counts},
    }
    return manifest, preserved_paths


def clean_text_for_reply_context(text: str) -> str:
    """Apply the production reply-context text transformation exactly."""
    text = html.unescape(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.split())
    return text.strip()


def trim_context_text(text: str, max_chars: int) -> str:
    """Apply the production bounded-context trimming semantics exactly."""
    text = clean_text_for_reply_context(text)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    prefix = text[: max_chars - 3].rsplit(" ", 1)[0].rstrip(".,;:")
    if not prefix:
        prefix = text[: max_chars - 3]
    return prefix + "..."


def render_cached_quote_context(
    exact_cached_text: str | None,
    exact_image_summary: str | None,
) -> tuple[str, str]:
    """Render a cache record through production ``tweet_context_text`` semantics."""
    cleaned = clean_text_for_reply_context(exact_cached_text or "")
    if cleaned:
        return (
            trim_context_text(cleaned, QUOTE_CONTEXT_MAX_CHARS),
            "production_cached_text_media_url_removal",
        )
    image_summary = clean_text_for_reply_context(exact_image_summary or "")
    if image_summary:
        wrapped = f"[Image/meme summary: {image_summary}]"
        return (
            trim_context_text(wrapped, QUOTE_CONTEXT_MAX_CHARS),
            "production_image_meme_summary_wrapper",
        )
    return "", "production_no_usable_cached_context"


def _read_regular_file_read_only(path: Path, *, label: str) -> tuple[bytes, int, str]:
    """Read one non-symlink regular file without following the final component."""
    try:
        if path.is_symlink():
            raise PackError(f"symlinked {label} refused")
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError(f"unable to open {label} read-only") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PackError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise PackError(f"unable to read {label}") from exc
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
    ):
        raise PackError(f"{label} changed while it was being read")
    content = b"".join(chunks)
    if len(content) != before.st_size:
        raise PackError(f"short read from {label}")
    return content, before.st_size, hashlib.sha256(content).hexdigest()


def _safe_snapshot_project_relative_path(value: Path | str) -> PurePosixPath:
    raw = str(value)
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        not raw
        or posix.is_absolute()
        or windows.is_absolute()
        or ".." in posix.parts
        or ".." in windows.parts
        or "\\" in raw
    ):
        raise PackError("snapshot project relative path is unsafe")
    return posix


def verify_history_manifest(
    history_corpus: Path,
    snapshot_root: Path,
    snapshot_project_relative_path: Path | str,
) -> dict[str, Any]:
    """Verify the immutable history manifest and its bounded snapshot selection."""
    try:
        history_corpus = history_corpus.resolve(strict=True)
        snapshot_root_raw = snapshot_root
        if snapshot_root_raw.is_symlink():
            raise PackError("symlinked snapshot root refused")
        snapshot_root = snapshot_root_raw.resolve(strict=True)
    except OSError as exc:
        raise PackError("history corpus or snapshot root is missing") from exc
    if not history_corpus.is_dir() or not snapshot_root.is_dir():
        raise PackError("history corpus and snapshot root must be directories")
    project_relative = _safe_snapshot_project_relative_path(snapshot_project_relative_path)
    manifest_path = history_corpus / "run_manifest.json"
    manifest_bytes, manifest_size, manifest_sha256 = _read_regular_file_read_only(
        manifest_path,
        label="history run manifest",
    )
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackError("history run manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise PackError("history run manifest root must be an object")
    expected = {
        "/schema_version": (manifest.get("schema_version"), HISTORY_SCHEMA_VERSION),
        "/tool_version": (manifest.get("tool_version"), HISTORY_TOOL_VERSION),
        "/extractor_git_commit": (manifest.get("extractor_git_commit"), SOURCE_EXTRACTOR_COMMIT),
        "/extractor_git_commit_confidence": (
            manifest.get("extractor_git_commit_confidence"),
            HISTORY_EXTRACTOR_COMMIT_CONFIDENCE,
        ),
        "/live_project_included": (manifest.get("live_project_included"), False),
    }
    for path, (actual, required) in expected.items():
        if actual != required:
            raise PackError(f"history manifest mismatch at {path}")
    snapshots = manifest.get("selected_snapshots")
    if (
        not isinstance(snapshots, list)
        or len(snapshots) != HISTORY_SELECTED_SNAPSHOT_COUNT
        or len(set(snapshots)) != HISTORY_SELECTED_SNAPSHOT_COUNT
    ):
        raise PackError("history manifest must select exactly 32 unique snapshots")
    for snapshot in snapshots:
        if (
            not isinstance(snapshot, str)
            or not snapshot
            or Path(snapshot).name != snapshot
            or snapshot in {".", ".."}
        ):
            raise PackError("history manifest contains an unsafe snapshot name")
    arguments = manifest.get("arguments")
    if not isinstance(arguments, dict):
        raise PackError("history manifest arguments are invalid")
    if arguments.get("project_relative_path") != project_relative.as_posix():
        raise PackError("history manifest project relative path disagrees")
    manifest_snapshot_root = arguments.get("snapshot_root")
    if not isinstance(manifest_snapshot_root, str):
        raise PackError("history manifest snapshot root is invalid")
    try:
        recorded_snapshot_root = Path(manifest_snapshot_root).resolve(strict=True)
    except OSError as exc:
        raise PackError("history manifest snapshot root is missing") from exc
    if recorded_snapshot_root != snapshot_root:
        raise PackError("history manifest snapshot root disagrees")
    return {
        "history_corpus": history_corpus,
        "snapshot_root": snapshot_root,
        "snapshot_project_relative_path": project_relative,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_size": manifest_size,
        "manifest_sha256": manifest_sha256,
        "selected_snapshots": snapshots,
    }


def _snapshot_bot_state_path(
    snapshot_root: Path,
    snapshot_name: str,
    project_relative: PurePosixPath,
) -> tuple[Path, Path]:
    snapshot_candidate = snapshot_root / snapshot_name
    try:
        if snapshot_candidate.is_symlink():
            raise PackError(f"symlinked selected snapshot refused: {snapshot_name}")
        snapshot = snapshot_candidate.resolve(strict=True)
    except OSError as exc:
        raise PackError(f"selected snapshot is missing: {snapshot_name}") from exc
    if not snapshot.is_dir() or not snapshot.is_relative_to(snapshot_root):
        raise PackError(f"selected snapshot is outside snapshot root: {snapshot_name}")
    cursor = snapshot
    for component in (*project_relative.parts, "bot_state.json"):
        cursor = cursor / component
        try:
            if cursor.is_symlink():
                raise PackError(f"symlinked bot_state path refused: {snapshot_name}")
            cursor.lstat()
        except OSError as exc:
            raise PackError(f"snapshot bot_state is missing: {snapshot_name}") from exc
    try:
        bot_state = cursor.resolve(strict=True)
    except OSError as exc:
        raise PackError(f"snapshot bot_state is missing: {snapshot_name}") from exc
    if not bot_state.is_relative_to(snapshot):
        raise PackError(f"snapshot bot_state is outside selected snapshot: {snapshot_name}")
    return snapshot, bot_state


def _optional_exact_cache_string(
    record: Mapping[str, Any],
    field: str,
    candidate_ids: Sequence[str],
) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PackError(
            "tweet-cache field has an invalid type",
            candidate_fields={candidate_id: [field] for candidate_id in candidate_ids},
        )
    return value


def _non_empty_occurrence_values(
    occurrences: Sequence[Mapping[str, Any]],
    field: str,
) -> set[str]:
    return {
        value
        for occurrence in occurrences
        if isinstance((value := occurrence.get(field)), str) and value != ""
    }


def recover_quote_contexts(
    requirements: Mapping[str, str],
    *,
    history_corpus: Path,
    snapshot_root: Path,
    snapshot_project_relative_path: Path | str,
) -> dict[str, Any]:
    """Recover exact selected account-post context from manifest-bounded caches."""
    verified = verify_history_manifest(
        history_corpus,
        snapshot_root,
        snapshot_project_relative_path,
    )
    snapshots: list[str] = verified["selected_snapshots"]
    by_quoted_id: dict[str, list[str]] = defaultdict(list)
    for candidate_id, quoted_post_id in requirements.items():
        if not isinstance(quoted_post_id, str) or not quoted_post_id:
            raise PackError(
                "quote-tweet recovery requires one exact quoted_post_id",
                candidate_fields={candidate_id: ["quoted_post_id"]},
            )
        by_quoted_id[quoted_post_id].append(candidate_id)

    occurrences: dict[str, list[dict[str, Any]]] = {
        candidate_id: [] for candidate_id in requirements
    }
    opened_files: list[dict[str, Any]] = []
    for snapshot_index, snapshot_name in enumerate(snapshots):
        _, bot_state_path = _snapshot_bot_state_path(
            verified["snapshot_root"],
            snapshot_name,
            verified["snapshot_project_relative_path"],
        )
        content, source_file_size, source_file_sha256 = _read_regular_file_read_only(
            bot_state_path,
            label=f"snapshot bot_state.json ({snapshot_name})",
        )
        opened_files.append(
            {
                "source_snapshot": snapshot_name,
                "source_path": str(bot_state_path),
                "source_file_size": source_file_size,
                "source_file_sha256": source_file_sha256,
            }
        )
        try:
            state = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PackError(f"snapshot bot_state JSON is invalid: {snapshot_name}") from exc
        tweet_cache = state.get("tweet_cache") if isinstance(state, dict) else None
        if not isinstance(tweet_cache, dict):
            raise PackError(f"snapshot tweet_cache is invalid: {snapshot_name}")
        for quoted_post_id, candidate_ids in by_quoted_id.items():
            cache_record = tweet_cache.get(quoted_post_id)
            if cache_record is None:
                continue
            if not isinstance(cache_record, dict):
                raise PackError(
                    "exact tweet-cache entry is not an object",
                    candidate_fields={
                        candidate_id: ["tweet_cache"] for candidate_id in candidate_ids
                    },
                )
            cache_id = cache_record.get("id")
            if cache_id not in (None, quoted_post_id):
                raise PackError(
                    "tweet-cache key and record identity disagree",
                    candidate_fields={candidate_id: ["quoted_post_id"] for candidate_id in candidate_ids},
                )
            occurrence = {
                "source_snapshot": snapshot_name,
                "source_path": str(bot_state_path),
                "source_file_size": source_file_size,
                "source_file_sha256": source_file_sha256,
                "cache_record_sha256": hashlib.sha256(
                    stable_json(cache_record).encode("utf-8")
                ).hexdigest(),
                "post_type": _optional_exact_cache_string(
                    cache_record, "post_type", candidate_ids
                ),
                "exact_cached_text": _optional_exact_cache_string(
                    cache_record, "text", candidate_ids
                ),
                "exact_image_summary": _optional_exact_cache_string(
                    cache_record, "image_summary", candidate_ids
                ),
                "author_id": _optional_exact_cache_string(
                    cache_record, "author_id", candidate_ids
                ),
                "conversation_id": _optional_exact_cache_string(
                    cache_record, "conversation_id", candidate_ids
                ),
                "created_at": _optional_exact_cache_string(
                    cache_record, "created_at", candidate_ids
                ),
                "_snapshot_index": snapshot_index,
            }
            for candidate_id in candidate_ids:
                occurrences[candidate_id].append(dict(occurrence))

    recoveries: dict[str, dict[str, Any]] = {}
    failure_fields: dict[str, list[str]] = defaultdict(list)
    conflict_fields: dict[str, list[str]] = defaultdict(list)
    author_ids_by_candidate: dict[str, set[str]] = {}
    compared_fields = (
        "author_id",
        "post_type",
        "conversation_id",
        "created_at",
        "exact_cached_text",
        "exact_image_summary",
    )
    for candidate_id in sorted(requirements):
        candidate_occurrences = occurrences[candidate_id]
        if not candidate_occurrences:
            failure_fields[candidate_id].extend(
                ["exact_cached_text", "exact_image_summary"]
            )
            continue
        conflicts = [
            field
            for field in compared_fields
            if len(_non_empty_occurrence_values(candidate_occurrences, field)) > 1
        ]
        if conflicts:
            conflict_fields[candidate_id].extend(conflicts)
            continue
        observed_post_types = _non_empty_occurrence_values(
            candidate_occurrences, "post_type"
        )
        if not observed_post_types.issubset(ACCOUNT_QUOTED_POST_TYPES):
            failure_fields[candidate_id].append("post_type")
            continue
        author_ids_by_candidate[candidate_id] = _non_empty_occurrence_values(
            candidate_occurrences, "author_id"
        )
        complete: list[tuple[dict[str, Any], str, str]] = []
        for occurrence in candidate_occurrences:
            rendered, method = render_cached_quote_context(
                occurrence["exact_cached_text"],
                occurrence["exact_image_summary"],
            )
            if rendered:
                complete.append((occurrence, rendered, method))
        if not complete:
            failure_fields[candidate_id].extend(
                ["exact_cached_text", "exact_image_summary"]
            )
            continue
        selected_occurrence, rendered_model_text, rendering_method = min(
            complete,
            key=lambda item: item[0]["_snapshot_index"],
        )
        public_occurrences = []
        for occurrence in candidate_occurrences:
            public_occurrence = dict(occurrence)
            public_occurrence.pop("_snapshot_index")
            public_occurrences.append(public_occurrence)
        recoveries[candidate_id] = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "case_pack_version": CASE_PACK_VERSION,
            "candidate_id": candidate_id,
            "quoted_post_id": requirements[candidate_id],
            "recovery_status": "exact_snapshot_cache",
            "recovery_confidence": "exact",
            "selected_snapshot": selected_occurrence["source_snapshot"],
            "corroborating_snapshot_count": len(candidate_occurrences),
            "source_occurrences": public_occurrences,
            "source_exact_text": selected_occurrence["exact_cached_text"],
            "source_exact_image_summary": selected_occurrence["exact_image_summary"],
            "rendered_model_text": rendered_model_text,
            "transformation_name": "production_tweet_context_text_and_trim_context_text",
            "text_rendering_method": rendering_method,
            "conflict_count": 0,
            "rendered_quoted_post": {
                "post_id": requirements[candidate_id],
                "author_role": "account",
                "text": rendered_model_text,
            },
            "validator_result": None,
        }
    if conflict_fields:
        raise PackError(
            "conflicting non-empty quote-context cache values",
            candidate_fields=conflict_fields,
        )
    if failure_fields:
        raise PackError(
            "quoted-post context is not recoverable from selected snapshots",
            candidate_fields=failure_fields,
        )
    all_author_ids = set().union(*author_ids_by_candidate.values()) if author_ids_by_candidate else set()
    if len(all_author_ids) > 1:
        raise PackError(
            "conflicting account author identity across recovered quote contexts",
            candidate_fields={candidate_id: ["author_id"] for candidate_id in requirements},
        )
    return {
        "recoveries": recoveries,
        "history_manifest": verified["manifest"],
        "history_manifest_path": str(verified["manifest_path"]),
        "history_manifest_size": verified["manifest_size"],
        "history_manifest_sha256": verified["manifest_sha256"],
        "selected_snapshots": snapshots,
        "snapshot_bot_state_files": opened_files,
        "account_author_id": next(iter(all_author_ids), None),
        "account_identity_basis": (
            "selected_account_quote_targets_and_cross_snapshot_author_id_agreement"
            if all_author_ids
            else "selected_account_quote_targets_author_id_unavailable"
        ),
    }


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise PackError(f"invalid JSONL row in {path.name} at line {line_number}") from exc
                if not isinstance(value, dict):
                    raise PackError(f"non-object JSONL row in {path.name} at line {line_number}")
                yield line_number, value
    except (OSError, UnicodeError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError(f"unable to read {path.name}") from exc


def load_candidate_sources(
    shortlist: Path,
    selected_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    eligible_by_id: dict[str, dict[str, Any]] = {}
    normalised_by_id: dict[str, dict[str, Any]] = {}
    all_eligible: list[dict[str, Any]] = []
    duplicate_fields: dict[str, list[str]] = defaultdict(list)

    for _, row in iter_jsonl(shortlist / "evaluation_eligible_candidates.jsonl"):
        candidate_id = row.get("candidate_id")
        if isinstance(candidate_id, str):
            all_eligible.append(row)
            if candidate_id in selected_ids:
                if candidate_id in eligible_by_id:
                    duplicate_fields[candidate_id].append("evaluation_eligible_candidates.jsonl")
                else:
                    eligible_by_id[candidate_id] = row
    for _, row in iter_jsonl(shortlist / "normalised_candidates.jsonl"):
        candidate_id = row.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id in selected_ids:
            if candidate_id in normalised_by_id:
                duplicate_fields[candidate_id].append("normalised_candidates.jsonl")
            else:
                normalised_by_id[candidate_id] = row
    if duplicate_fields:
        raise PackError("duplicate selected candidate row", candidate_fields=duplicate_fields)
    missing: dict[str, list[str]] = defaultdict(list)
    for candidate_id in sorted(selected_ids):
        if candidate_id not in eligible_by_id:
            missing[candidate_id].append("evaluation_eligible_candidates.jsonl")
        if candidate_id not in normalised_by_id:
            missing[candidate_id].append("normalised_candidates.jsonl")
    if missing:
        raise PackError("selected candidate row is missing", candidate_fields=missing)
    return eligible_by_id, normalised_by_id, all_eligible


def compare_join_rows(
    selected_rows: Sequence[dict[str, Any]],
    eligible: Mapping[str, dict[str, Any]],
    normalised: Mapping[str, dict[str, Any]],
) -> None:
    agreement_fields = (
        "candidate_id",
        "lane",
        "target_id",
        "incoming_text",
        "normalised_outcome",
        "normalised_reconstruction_status",
    )
    failures: dict[str, list[str]] = defaultdict(list)
    for selected in selected_rows:
        candidate_id = selected["candidate_id"]
        eligible_row = eligible[candidate_id]
        normalised_row = normalised[candidate_id]
        for field in agreement_fields:
            if eligible_row.get(field) != normalised_row.get(field):
                failures[candidate_id].append(field)
        if normalised_row.get("normalised_reconstruction_status") != "complete":
            failures[candidate_id].append("normalised_reconstruction_status")
        if normalised_row.get("remaining_conflict_evidence"):
            failures[candidate_id].append("remaining_conflict_evidence")
        selected_source_fields = {
            "lane": "lane",
            "incoming_text": "incoming_text",
            "prompt_era_id": "prompt_era_id",
            "historical_outcome": "normalised_outcome",
        }
        for selection_field, source_field in selected_source_fields.items():
            if selection_field in selected and selected.get(selection_field) != normalised_row.get(source_field):
                failures[candidate_id].append(selection_field)
        selected_reply = selected.get("historical_reply")
        source_reply = normalised_row.get("actual_reply_text")
        if selected_reply not in (None, "", "(none)"):
            if selected_reply != source_reply:
                failures[candidate_id].append("historical_reply")
        elif source_reply not in (None, ""):
            failures[candidate_id].append("historical_reply")
    if failures:
        raise PackError("selected and source candidate records disagree", candidate_fields=failures)


def map_lane(historical_lane: object, candidate_id: str) -> str:
    if not isinstance(historical_lane, str) or historical_lane not in LANE_MAP:
        raise PackError(
            "selected lane is unsupported",
            candidate_fields={candidate_id: ["lane"]},
        )
    return LANE_MAP[historical_lane]


def adapt_context_post(value: object, candidate_id: str, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"post_id", "author_role", "text"}:
        raise PackError(
            "bounded context cannot be adapted without invention",
            candidate_fields={candidate_id: [field]},
        )
    post_id = value.get("post_id")
    author_role = value.get("author_role")
    text = value.get("text")
    if (
        not isinstance(post_id, str)
        or not post_id
        or author_role not in {"account", "user", "unknown"}
        or not isinstance(text, str)
    ):
        raise PackError(
            "bounded context cannot be adapted without invention",
            candidate_fields={candidate_id: [field]},
        )
    return {"post_id": post_id, "author_role": author_role, "text": text}


def build_validated_context(
    row: dict[str, Any],
    current_lane: str,
    candidate_id: str,
    current_date: str,
    quote_recovery: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    target_id = row.get("target_id")
    incoming = row.get("incoming_text")
    source_thread_id = row.get("thread_id")
    if not isinstance(target_id, str) or not target_id:
        raise PackError("candidate context is incomplete", candidate_fields={candidate_id: ["target_id"]})
    if not isinstance(incoming, str) or not incoming.strip():
        raise PackError("candidate context is incomplete", candidate_fields={candidate_id: ["incoming_text"]})
    if isinstance(source_thread_id, str) and source_thread_id:
        thread_id = source_thread_id
        identity_adaptation = "source_thread_id"
    else:
        thread_id = target_id
        identity_adaptation = "target_id_as_context_identity"

    quoted: dict[str, str] | None = None
    quoted_id = row.get("quoted_post_id")
    quoted_text = row.get("quoted_post_text")
    if quote_recovery is not None:
        rendered = quote_recovery.get("rendered_quoted_post")
        if (
            not isinstance(quoted_id, str)
            or not quoted_id
            or not isinstance(rendered, dict)
            or rendered.get("post_id") != quoted_id
            or rendered.get("author_role") != "account"
            or not isinstance(rendered.get("text"), str)
            or not rendered["text"]
        ):
            raise PackError(
                "recovered quoted-post context is invalid",
                candidate_fields={candidate_id: ["quoted_post_id", "quoted_post_text"]},
            )
        quoted = {
            "post_id": quoted_id,
            "author_role": "account",
            "text": rendered["text"],
        }
    elif quoted_id not in (None, "") or quoted_text not in (None, ""):
        if (
            not isinstance(quoted_id, str)
            or not quoted_id
            or not isinstance(quoted_text, str)
            or not clean_text_for_reply_context(quoted_text)
        ):
            raise PackError(
                "quoted-post context is incomplete",
                candidate_fields={candidate_id: ["quoted_post_id", "quoted_post_text"]},
            )
        quoted = {"post_id": quoted_id, "author_role": "unknown", "text": quoted_text}

    parents_value = row.get("bounded_parent_context")
    if parents_value is None:
        parents: list[dict[str, str]] = []
    elif isinstance(parents_value, list):
        parents = [
            adapt_context_post(parent, candidate_id, "bounded_parent_context")
            for parent in parents_value
        ]
    else:
        raise PackError(
            "bounded context cannot be adapted without invention",
            candidate_fields={candidate_id: ["bounded_parent_context"]},
        )

    clarification: dict[str, str] | None = None
    if "clarification_request" in row and row.get("clarification_request") is not None:
        value = row["clarification_request"]
        if not isinstance(value, dict) or set(value) != {"original_question", "correction"}:
            raise PackError(
                "clarification context is malformed",
                candidate_fields={candidate_id: ["clarification_request"]},
            )
        clarification = {
            "original_question": value.get("original_question"),
            "correction": value.get("correction"),
        }

    context = {
        "target_id": target_id,
        "thread_id": thread_id,
        "lane": current_lane,
        "incoming_contribution": incoming,
        "quoted_post": quoted,
        "parent_thread": parents,
        "clarification_request": clarification,
        "current_date": current_date,
    }
    try:
        validated = reply_strategy.validate_reply_context(context)
    except (TypeError, ValueError) as exc:
        raise PackError(
            "current reply context validation failed",
            candidate_fields={candidate_id: ["validated_context"]},
        ) from exc
    return validated, identity_adaptation


def build_recent_reply_index(all_eligible: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    posted: list[dict[str, Any]] = []
    failures: dict[str, list[str]] = defaultdict(list)
    for row in all_eligible:
        if row.get("normalised_outcome") != "posted":
            continue
        reply_text = row.get("actual_reply_text")
        terminal_timestamp = row.get("terminal_timestamp")
        candidate_id = row.get("candidate_id")
        if not isinstance(reply_text, str) or not reply_text or not isinstance(terminal_timestamp, str):
            continue
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        try:
            instant = parse_timestamp(terminal_timestamp, "terminal_timestamp", candidate_id)
        except PackError:
            failures[candidate_id].append("terminal_timestamp")
            continue
        posted.append(
            {
                "candidate_id": candidate_id,
                "terminal_timestamp": terminal_timestamp,
                "reply_text": reply_text,
                "_instant": instant,
            }
        )
    if failures:
        raise PackError("eligible posted reply timestamp is invalid", candidate_fields=failures)
    posted.sort(key=lambda item: (item["_instant"], item["candidate_id"]))
    return posted


def recent_replies_for_case(
    candidate_id: str,
    first_timestamp: object,
    posted_index: Sequence[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, str]], list[str]]:
    first = parse_timestamp(first_timestamp, "first_timestamp", candidate_id)
    eligible = [
        item
        for item in posted_index
        if item["_instant"] < first and item["candidate_id"] != candidate_id
    ]
    selected = eligible[-limit:] if limit else []
    records = [
        {
            "candidate_id": item["candidate_id"],
            "terminal_timestamp": item["terminal_timestamp"],
            "reply_text": item["reply_text"],
        }
        for item in selected
    ]
    return records, [item["reply_text"] for item in selected]


def walk_scalars(value: object, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield path + (str(key),), str(key)
            yield from walk_scalars(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_scalars(child, path + (str(index),))
    else:
        yield path, value


def audit_payload(
    candidate_id: str,
    payload_name: str,
    payload: object,
    historical_reply: object = None,
) -> list[dict[str, str]]:
    """Audit forbidden names/values, with optional unqualified text leakage.

    Case construction uses :func:`audit_case_inputs` for historical reply text
    because recent reply text requires candidate and timestamp provenance.
    """
    violations: list[dict[str, str]] = []
    for path, scalar in walk_scalars(payload):
        if not isinstance(scalar, str):
            continue
        for forbidden in FORBIDDEN_MODEL_FIELDS:
            if scalar == forbidden or forbidden in scalar:
                violations.append(
                    {
                        "candidate_id": candidate_id,
                        "payload": payload_name,
                        "path": "/" + "/".join(path),
                        "kind": f"forbidden_field:{forbidden}",
                    }
                )
        if (
            isinstance(historical_reply, str)
            and historical_reply
            and historical_reply != "(none)"
            and historical_reply in scalar
        ):
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": payload_name,
                    "path": "/" + "/".join(path),
                    "kind": "exact_historical_reply",
                }
            )
    return violations


def _text_occurrences(value: object, needle: str, path: tuple[str, ...] = ()) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _text_occurrences(child, needle, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _text_occurrences(child, needle, path + (str(index),))
    elif isinstance(value, str) and needle in value:
        yield "/" + "/".join(path)


def audit_case_inputs(
    *,
    candidate_id: str,
    first_timestamp: object,
    historical_reply: object,
    model_record: dict[str, Any],
    recent_record: dict[str, Any],
    proposer_payload: dict[str, Any],
    posted_index: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Apply per-case leakage checks using eligible-posted provenance."""
    violations: list[dict[str, str]] = []
    for name, payload in (
        ("model_input", model_record),
        ("recent_account_replies", recent_record),
        ("prepared_prompt_payload", proposer_payload),
    ):
        violations.extend(audit_payload(candidate_id, name, payload))

    first = parse_timestamp(first_timestamp, "first_timestamp", candidate_id)
    posted_provenance = {
        (item["candidate_id"], item["terminal_timestamp"], item["reply_text"])
        for item in posted_index
    }
    structured = recent_record.get("recent_account_replies")
    text_only = recent_record.get("recent_account_replies_text")
    if not isinstance(structured, list) or not isinstance(text_only, list):
        violations.append(
            {
                "candidate_id": candidate_id,
                "payload": "recent_account_replies",
                "path": "/recent_account_replies",
                "kind": "recent_reply_provenance_invalid",
            }
        )
        structured = []
        text_only = []

    authorised: list[dict[str, str]] = []
    authorised_same_text: list[dict[str, str]] = []
    structured_text: list[object] = []
    for index, item in enumerate(structured):
        path = f"/recent_account_replies/{index}"
        if not isinstance(item, dict) or set(item) != {
            "candidate_id",
            "terminal_timestamp",
            "reply_text",
        }:
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "recent_account_replies",
                    "path": path,
                    "kind": "recent_reply_provenance_invalid",
                }
            )
            continue
        source_id = item.get("candidate_id")
        terminal = item.get("terminal_timestamp")
        reply_text = item.get("reply_text")
        structured_text.append(reply_text)
        if source_id == candidate_id:
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "recent_account_replies",
                    "path": f"{path}/candidate_id",
                    "kind": "self_answer_candidate_id",
                }
            )
            continue
        provenance = (source_id, terminal, reply_text)
        if provenance not in posted_provenance:
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "recent_account_replies",
                    "path": path,
                    "kind": "recent_reply_not_eligible_posted",
                }
            )
            continue
        try:
            terminal_instant = parse_timestamp(terminal, "terminal_timestamp", candidate_id)
        except PackError:
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "recent_account_replies",
                    "path": f"{path}/terminal_timestamp",
                    "kind": "recent_reply_chronology",
                }
            )
            continue
        if terminal_instant >= first:
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "recent_account_replies",
                    "path": f"{path}/terminal_timestamp",
                    "kind": "recent_reply_chronology",
                }
            )
            continue
        authorised_item = {
            "case_candidate_id": candidate_id,
            "source_candidate_id": str(source_id),
        }
        authorised.append(authorised_item)
        if (
            isinstance(historical_reply, str)
            and historical_reply
            and historical_reply != "(none)"
            and reply_text == historical_reply
        ):
            authorised_same_text.append(authorised_item)

    if structured_text != text_only:
        violations.append(
            {
                "candidate_id": candidate_id,
                "payload": "recent_account_replies",
                "path": "/recent_account_replies_text",
                "kind": "recent_reply_text_provenance_mismatch",
            }
        )
    if model_record.get("recent_account_replies_text") != text_only:
        violations.append(
            {
                "candidate_id": candidate_id,
                "payload": "model_input",
                "path": "/recent_account_replies_text",
                "kind": "recent_reply_text_provenance_mismatch",
            }
        )
    proposer_recent = proposer_payload.get("recent_account_replies_to_avoid_repeating")
    if proposer_recent != text_only:
        violations.append(
            {
                "candidate_id": candidate_id,
                "payload": "prepared_prompt_payload",
                "path": "/recent_account_replies_to_avoid_repeating",
                "kind": "recent_reply_text_provenance_mismatch",
            }
        )

    if isinstance(historical_reply, str) and historical_reply and historical_reply != "(none)":
        validated_context = model_record.get("validated_context")
        for path in _text_occurrences(validated_context, historical_reply):
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "validated_context",
                    "path": path,
                    "kind": "self_answer_historical_reply",
                }
            )
        matching_provenance = sum(
            1
            for item in structured
            if isinstance(item, dict)
            and item.get("candidate_id") != candidate_id
            and item.get("reply_text") == historical_reply
            and (item.get("candidate_id"), item.get("terminal_timestamp"), item.get("reply_text"))
            in posted_provenance
            and parse_timestamp(item.get("terminal_timestamp"), "terminal_timestamp", candidate_id)
            < first
        )
        text_occurrences = sum(
            1 for value in text_only if isinstance(value, str) and historical_reply in value
        )
        if text_occurrences > matching_provenance:
            violations.append(
                {
                    "candidate_id": candidate_id,
                    "payload": "recent_account_replies",
                    "path": "/recent_account_replies_text",
                    "kind": "self_answer_historical_reply",
                }
            )

    return {
        "violations": violations,
        "authorised_cross_case_recent_replies": authorised,
        "authorised_same_text_recent_replies": authorised_same_text,
    }


def _baseline(row: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "candidate_id": candidate_id,
        "historical_outcome": row.get("normalised_outcome"),
        "historical_reply": row.get("actual_reply_text"),
        "historical_mode": row.get("normalised_mode"),
        "historical_tone": row.get("normalised_tone"),
        "historical_no_reply_reason": row.get("normalised_no_reply_reason"),
        "historical_deterministic_rejection_reason": row.get(
            "normalised_deterministic_rejection_reason"
        ),
        "historical_reviewer_verdict": row.get("normalised_reviewer_verdict"),
        "historical_model_call_count": row.get("normalised_model_call_count"),
        "historical_revision_count": row.get("normalised_revision_count"),
    }


def _case_sort_key(case: Mapping[str, Any]) -> tuple[int, int, str]:
    return (
        FINAL_STRATA.index(str(case["final_stratum"])),
        int(case["final_rank"]),
        str(case["candidate_id"]),
    )


def construct_cases(
    selected_rows: Sequence[dict[str, Any]],
    normalised: Mapping[str, dict[str, Any]],
    all_eligible: Sequence[dict[str, Any]],
    *,
    current_date: str,
    recent_reply_limit: int,
    quote_recoveries: Mapping[str, dict[str, Any]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    posted_index = build_recent_reply_index(all_eligible)
    frozen_cases: list[dict[str, Any]] = []
    model_inputs: list[dict[str, Any]] = []
    baselines: list[dict[str, Any]] = []
    recent_outputs: list[dict[str, Any]] = []
    violations: list[dict[str, str]] = []
    authorised_cross_case: list[dict[str, str]] = []
    authorised_same_text: list[dict[str, str]] = []
    not_ready: dict[str, list[str]] = defaultdict(list)
    quote_recoveries = quote_recoveries or {}

    for selected in selected_rows:
        candidate_id = selected["candidate_id"]
        source = normalised[candidate_id]
        try:
            current_lane = map_lane(source.get("lane"), candidate_id)
            quote_recovery = quote_recoveries.get(candidate_id)
            validated, identity_adaptation = build_validated_context(
                source,
                current_lane,
                candidate_id,
                current_date,
                quote_recovery=quote_recovery,
            )
            if quote_recovery is not None:
                quote_recovery["validator_result"] = {
                    "status": "pass",
                    "validator": "reply_strategy.validate_reply_context",
                    "validated_context_sha256": hashlib.sha256(
                        stable_json(validated).encode("utf-8")
                    ).hexdigest(),
                }
            recent_records, recent_text = recent_replies_for_case(
                candidate_id,
                source.get("first_timestamp"),
                posted_index,
                recent_reply_limit,
            )
        except PackError as exc:
            for failed_id, fields in exc.candidate_fields.items():
                not_ready[failed_id].extend(fields)
            if not exc.candidate_fields:
                not_ready[candidate_id].append("context")
            continue

        if current_lane == "quote_tweet" and validated["quoted_post"] is None:
            not_ready[candidate_id].extend(["quoted_post_id", "quoted_post_text"])

        chronological = all(
            parse_timestamp(item["terminal_timestamp"], "terminal_timestamp", candidate_id)
            < parse_timestamp(source.get("first_timestamp"), "first_timestamp", candidate_id)
            for item in recent_records
        )
        if not chronological:
            not_ready[candidate_id].append("recent_account_replies")

        model_record = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "candidate_id": candidate_id,
            "current_pipeline_lane": current_lane,
            "validated_context": validated,
            "recent_account_replies_text": recent_text,
        }
        recent_record = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "candidate_id": candidate_id,
            "recent_account_replies": recent_records,
            "recent_account_replies_text": recent_text,
        }
        _, proposer_payload_json = reply_strategy._proposer_prompts(
            validated,
            recent_text,
            resolved_quotation=None,
            revision=None,
        )
        proposer_payload = json.loads(proposer_payload_json)
        historical_reply = source.get("actual_reply_text")
        case_audit = audit_case_inputs(
            candidate_id=candidate_id,
            first_timestamp=source.get("first_timestamp"),
            historical_reply=historical_reply,
            model_record=model_record,
            recent_record=recent_record,
            proposer_payload=proposer_payload,
            posted_index=posted_index,
        )
        case_violations = case_audit["violations"]
        if case_violations:
            violations.extend(case_violations)
            not_ready[candidate_id].append("leakage_audit")
        authorised_cross_case.extend(case_audit["authorised_cross_case_recent_replies"])
        authorised_same_text.extend(case_audit["authorised_same_text_recent_replies"])

        baseline = _baseline(source, candidate_id)
        candidate_context = {
            "target_id": source.get("target_id"),
            "author_id": source.get("author_id"),
            "thread_id": source.get("thread_id"),
            "incoming_text": source.get("incoming_text"),
            "quoted_post_id": source.get("quoted_post_id"),
            "quoted_post_text": source.get("quoted_post_text"),
            "bounded_parent_context": source.get("bounded_parent_context"),
        }
        if "clarification_request" in source and source.get("clarification_request") is not None:
            candidate_context["clarification_request"] = source["clarification_request"]
        frozen = {
            "schema_version": SCHEMA_VERSION,
            "case_pack_version": CASE_PACK_VERSION,
            "candidate_id": candidate_id,
            "final_stratum": selected["final_stratum"],
            "final_rank": selected["final_rank"],
            "selection_rationale": selected.get("reviewer_note"),
            "provisional_strata": selected.get("provisional_strata"),
            "source_row_sha256": source.get("source_row_sha256"),
            "prompt_era_id": source.get("prompt_era_id"),
            "prompt_version_evidence": source.get("prompt_version_evidence"),
            "strategy_version": source.get("strategy_version"),
            "first_timestamp": source.get("first_timestamp"),
            "terminal_timestamp": source.get("terminal_timestamp"),
            "source_record_ids": source.get("source_record_ids"),
            "supplemental_evidence_ids": source.get("supplemental_evidence_ids"),
            "historical_lane": source.get("lane"),
            "current_pipeline_lane": current_lane,
            "historical_baseline": baseline,
            "candidate_context": candidate_context,
            "validated_context": validated,
            "context_identity_adaptation": identity_adaptation,
            "recent_account_replies": recent_records,
            "recent_account_replies_text": recent_text,
            "quote_context_recovery": (
                {
                    "recovery_status": quote_recovery["recovery_status"],
                    "recovery_confidence": quote_recovery["recovery_confidence"],
                    "selected_snapshot": quote_recovery["selected_snapshot"],
                    "conflict_count": quote_recovery["conflict_count"],
                }
                if quote_recovery is not None
                else None
            ),
            "replay_ready": candidate_id not in not_ready,
        }
        frozen_cases.append(frozen)
        model_inputs.append(model_record)
        baselines.append(baseline)
        recent_outputs.append(recent_record)

    self_answer_violations = [
        item for item in violations if item["kind"].startswith("self_answer_")
    ]
    forbidden_key_violations = [
        item for item in violations if item["kind"].startswith("forbidden_field:")
    ]
    chronology_violations = [
        item for item in violations if item["kind"] == "recent_reply_chronology"
    ]
    provenance_violations = [
        item
        for item in violations
        if item["kind"]
        in {
            "recent_reply_provenance_invalid",
            "recent_reply_not_eligible_posted",
            "recent_reply_text_provenance_mismatch",
        }
    ]
    leakage_audit = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "forbidden_keys_checked": list(FORBIDDEN_MODEL_FIELDS),
        "exact_historical_reply_leakage_checks": len(model_inputs),
        "cases_checked": len(model_inputs),
        "self_answer_violations": len(self_answer_violations),
        "forbidden_key_violations": len(forbidden_key_violations),
        "chronology_violations": len(chronology_violations),
        "recent_reply_provenance_violations": len(provenance_violations),
        "authorised_cross_case_recent_reply_occurrences": len(authorised_cross_case),
        "authorised_same_text_recent_reply_occurrences": len(authorised_same_text),
        "authorised_cross_case_recent_replies": authorised_cross_case,
        "violations": violations,
        "result": "pass" if not violations else "fail",
    }
    if len(frozen_cases) != 48 or len(not_ready) or violations:
        if len(frozen_cases) != 48:
            present = {case["candidate_id"] for case in frozen_cases}
            for selected in selected_rows:
                if selected["candidate_id"] not in present:
                    not_ready[selected["candidate_id"]].append("constructed_case")
        raise PackError("fewer than 48 selected cases are replay-ready", candidate_fields=not_ready)
    return frozen_cases, model_inputs, baselines, recent_outputs, leakage_audit


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(WORKTREE), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackError("unable to read current Git provenance") from exc
    commit = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PackError("current Git provenance is invalid")
    return commit


def private_output_directory(
    output: Path,
    selection: Path,
    shortlist: Path,
    additional_protected_roots: Sequence[Path] = (),
) -> None:
    output_resolved = output.resolve()
    protected = (
        selection.resolve(),
        shortlist.resolve(),
        WORKTREE.resolve(),
        *(path.resolve() for path in additional_protected_roots),
    )
    if any(output_resolved == root or root in output_resolved.parents for root in protected):
        raise PackError("output path is inside a protected input or worktree tree")
    if output.exists():
        if not output.is_dir():
            raise PackError("output path exists and is not a directory")
        try:
            if any(output.iterdir()):
                raise PackError("output directory must be new or empty")
        except OSError as exc:
            raise PackError("unable to inspect output directory") from exc
        output.chmod(0o700)
    else:
        output.mkdir(mode=0o700, parents=False)
    if (output.stat().st_mode & 0o777) != 0o700:
        raise PackError("output directory permissions are not 0700")


def write_private_file(output: Path, name: str, content: bytes) -> None:
    path = output / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    if (path.stat().st_mode & 0o777) != 0o600:
        raise PackError(f"output file permissions are not 0600: {name}")


def json_bytes(value: object) -> bytes:
    return (stable_json(value) + "\n").encode("utf-8")


def jsonl_bytes(rows: Iterable[object]) -> bytes:
    return "".join(stable_json(row) + "\n" for row in rows).encode("utf-8")


def build_report(
    counts_by_stratum: Mapping[str, int],
    lane_counts: Mapping[str, int],
    outcome_counts: Mapping[str, int],
    prompt_era_count: int,
    recent_min: int,
    recent_max: int,
    selected_quote_tweet_count: int,
    recovered_quote_context_count: int,
) -> str:
    stratum_lines = "\n".join(f"- `{name}`: {counts_by_stratum[name]}" for name in FINAL_STRATA)
    lane_lines = "\n".join(f"- `{name}`: {lane_counts[name]}" for name in sorted(lane_counts))
    outcome_lines = "\n".join(f"- `{name}`: {outcome_counts[name]}" for name in sorted(outcome_counts))
    return f"""# Frozen conversational-reply replay pack

This pack is frozen input for private research. Historical outcomes are evidence, not target answers. Model input excludes historical output, and no model call occurred while preparing this pack. Missing context was not invented.

All 48 selected cases passed the current context validator and replay-readiness gates. The calibration subset contains six cases, one per final stratum. The full two-variant comparison plans 96 pipeline executions; actual provider calls may be higher because no-reply review, evidence adjudication, claim audit and revision can add calls.

## Counts by final stratum

{stratum_lines}

## Counts by current lane

{lane_lines}

## Historical outcomes

{outcome_lines}

- Prompt-era count: {prompt_era_count}
- Recent-account-reply coverage range: {recent_min}..{recent_max}
- Selected quote-tweet cases: {selected_quote_tweet_count}
- Snapshot-recovered quote contexts: {recovered_quote_context_count}
- Quote-context recovery conflicts: 0
- Quote-context recovery failures: 0
- Leakage violations: 0
- Model calls performed: 0
"""


def prepare_pack(
    *,
    selection: Path,
    shortlist: Path,
    output: Path,
    created_at: str,
    recent_reply_limit: int = 20,
    calibration_per_stratum: int = 1,
    history_corpus: Path | None = None,
    snapshot_root: Path | None = None,
    snapshot_project_relative_path: Path | str | None = None,
) -> dict[str, Any]:
    created_at = validate_created_at(created_at)
    if type(recent_reply_limit) is not int or recent_reply_limit < 0:
        raise PackError("recent-reply-limit must be a non-negative integer")
    if type(calibration_per_stratum) is not int or not 1 <= calibration_per_stratum <= 8:
        raise PackError("calibration-per-stratum must be between 1 and 8")
    selection = selection.resolve()
    shortlist = shortlist.resolve()
    output = output.resolve()
    if not selection.is_dir() or not shortlist.is_dir():
        raise PackError("selection and shortlist paths must be directories")
    additional_protected = [
        path
        for path in (history_corpus, snapshot_root)
        if isinstance(path, Path)
    ]
    private_output_directory(
        output,
        selection,
        shortlist,
        additional_protected_roots=additional_protected,
    )

    try:
        selected_rows, selection_document, selection_hashes = read_selection(selection)
        shortlist_hashes, checksum_file_hash = verify_shortlist_checksums(shortlist)
        shortlist_manifest, source_paths = verify_source_manifest(shortlist)
        selected_source = selection_document.get("source")
        if not isinstance(selected_source, dict):
            raise PackError("selection source provenance is invalid")
        if selected_source.get("source_extractor_commit") != SOURCE_EXTRACTOR_COMMIT:
            raise PackError("selection source extractor commit is invalid")
        if selected_source.get("shortlist_run_manifest_sha256") != shortlist_hashes["run_manifest.json"]:
            raise PackError("selection and shortlist manifest provenance disagree")
        selected_ids = {row["candidate_id"] for row in selected_rows}
        eligible, normalised, all_eligible = load_candidate_sources(shortlist, selected_ids)
        compare_join_rows(selected_rows, eligible, normalised)
        selected_quote_ids = [
            row["candidate_id"]
            for row in selected_rows
            if normalised[row["candidate_id"]].get("lane") == "quote-tweet"
        ]
        quote_contexts_present_in_source = 0
        quote_requirements: dict[str, str] = {}
        invalid_quote_ids: dict[str, list[str]] = {}
        for candidate_id in selected_quote_ids:
            source = normalised[candidate_id]
            quoted_post_id = source.get("quoted_post_id")
            quoted_post_text = source.get("quoted_post_text")
            if not isinstance(quoted_post_id, str) or not quoted_post_id:
                invalid_quote_ids[candidate_id] = ["quoted_post_id"]
                if (
                    not isinstance(quoted_post_text, str)
                    or not clean_text_for_reply_context(quoted_post_text)
                ):
                    invalid_quote_ids[candidate_id].append("quoted_post_text")
                continue
            if (
                isinstance(quoted_post_text, str)
                and clean_text_for_reply_context(quoted_post_text)
            ):
                quote_contexts_present_in_source += 1
            else:
                quote_requirements[candidate_id] = quoted_post_id
        if invalid_quote_ids:
            raise PackError(
                "quote-tweet recovery requires one exact quoted_post_id",
                candidate_fields=invalid_quote_ids,
            )

        recovery_bundle: dict[str, Any] = {
            "recoveries": {},
            "history_manifest": None,
            "history_manifest_path": None,
            "history_manifest_size": None,
            "history_manifest_sha256": None,
            "selected_snapshots": [],
            "snapshot_bot_state_files": [],
            "account_author_id": None,
            "account_identity_basis": None,
        }
        if quote_requirements:
            missing_arguments = [
                name
                for name, value in (
                    ("history_corpus", history_corpus),
                    ("snapshot_root", snapshot_root),
                    (
                        "snapshot_project_relative_path",
                        snapshot_project_relative_path,
                    ),
                )
                if value is None
            ]
            if missing_arguments:
                raise PackError(
                    "quote-context recovery arguments are required",
                    candidate_fields={
                        candidate_id: missing_arguments for candidate_id in quote_requirements
                    },
                )
            assert history_corpus is not None
            assert snapshot_root is not None
            assert snapshot_project_relative_path is not None
            recovery_bundle = recover_quote_contexts(
                quote_requirements,
                history_corpus=history_corpus,
                snapshot_root=snapshot_root,
                snapshot_project_relative_path=snapshot_project_relative_path,
            )
        quote_recoveries = recovery_bundle["recoveries"]
        frozen, model_inputs, baselines, recent_outputs, leakage_audit = construct_cases(
            selected_rows,
            normalised,
            all_eligible,
            current_date=created_at[:10],
            recent_reply_limit=recent_reply_limit,
            quote_recoveries=quote_recoveries,
        )

        frozen.sort(key=_case_sort_key)
        ordering = {case["candidate_id"]: _case_sort_key(case) for case in frozen}
        model_inputs.sort(key=lambda row: ordering[row["candidate_id"]])
        baselines.sort(key=lambda row: ordering[row["candidate_id"]])
        recent_outputs.sort(key=lambda row: ordering[row["candidate_id"]])
        recovery_rows = sorted(
            quote_recoveries.values(),
            key=lambda row: ordering[row["candidate_id"]],
        )
        if any(row.get("validator_result") is None for row in recovery_rows):
            raise PackError("recovered quote context did not pass the current validator")
        calibration = [
            {
                "schema_version": SCHEMA_VERSION,
                "candidate_id": case["candidate_id"],
                "final_stratum": case["final_stratum"],
                "final_rank": case["final_rank"],
                "calibration_role": STRATUM_LABELS[case["final_stratum"]],
                "purpose": "cost_and_pipeline_path_calibration_not_quality_result",
            }
            for case in frozen
            if case["final_rank"] <= calibration_per_stratum
        ]
        expected_calibration = len(FINAL_STRATA) * calibration_per_stratum
        if len(calibration) != expected_calibration or len({row["candidate_id"] for row in calibration}) != len(calibration):
            raise PackError("calibration subset is not unique and complete")

        replay_plan = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "case_pack_version": CASE_PACK_VERSION,
            "variants": ["current", "compact"],
            "historical_baseline": {"model_call": False},
            "calibration": {
                "cases": len(calibration),
                "live_variants": 2,
                "pipeline_executions": len(calibration) * 2,
            },
            "full_run": {"cases": 48, "live_variants": 2, "pipeline_executions": 96},
            "estimated_cost": "unavailable_pending_calibration",
            "model_calls_performed": 0,
            "provider_call_note": (
                "Pipeline executions may make multiple model calls because of no-reply review, "
                "evidence adjudication, claim audit and revision."
            ),
        }
        counts_by_stratum = dict(Counter(case["final_stratum"] for case in frozen))
        lane_counts = dict(Counter(case["current_pipeline_lane"] for case in frozen))
        outcome_counts = dict(
            Counter(case["historical_baseline"]["historical_outcome"] for case in frozen)
        )
        prompt_eras = sorted({case["prompt_era_id"] for case in frozen})
        recent_counts = [len(row["recent_account_replies"]) for row in recent_outputs]

        source_verification = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "selection": {
                "directory": str(selection),
                "input_sha256": selection_hashes,
                "schema_version": selection_document["schema_version"],
                "selection_status": selection_document["selection_status"],
                "selected_cases": 48,
                "unique_selected_candidates": 48,
                "counts_by_final_stratum": counts_by_stratum,
                "json_csv_agreement": True,
            },
            "shortlist": {
                "directory": str(shortlist),
                "sha256sums_sha256": checksum_file_hash,
                "verified_input_sha256": shortlist_hashes,
                "source_manifest_paths": source_paths,
                "checksums_verified": True,
            },
            "join": {
                "selected_candidates": 48,
                "eligible_rows_exactly_one": 48,
                "normalised_rows_exactly_one": 48,
                "source_agreement": True,
            },
            "quote_context_recovery": {
                "history_corpus": (
                    str(Path(history_corpus).resolve()) if history_corpus is not None else None
                ),
                "history_manifest_path": recovery_bundle["history_manifest_path"],
                "history_manifest_size": recovery_bundle["history_manifest_size"],
                "history_manifest_sha256": recovery_bundle["history_manifest_sha256"],
                "history_schema_version": (
                    recovery_bundle["history_manifest"].get("schema_version")
                    if recovery_bundle["history_manifest"] is not None
                    else None
                ),
                "history_tool_version": (
                    recovery_bundle["history_manifest"].get("tool_version")
                    if recovery_bundle["history_manifest"] is not None
                    else None
                ),
                "selected_snapshots": recovery_bundle["selected_snapshots"],
                "snapshot_bot_state_files": recovery_bundle[
                    "snapshot_bot_state_files"
                ],
                "account_author_id": recovery_bundle["account_author_id"],
                "account_identity_basis": recovery_bundle["account_identity_basis"],
                "result": "pass",
            },
            "result": "pass",
        }
        current_prompt_versions = {
            "strategy_version": reply_strategy.STRATEGY_VERSION,
            "proposer_prompt_version": reply_strategy.PROPOSER_PROMPT_VERSION,
            "evidence_prompt_version": reply_strategy.EVIDENCE_PROMPT_VERSION,
            "reviewer_prompt_version": reply_strategy.REVIEWER_PROMPT_VERSION,
            "no_reply_review_prompt_version": reply_strategy.NO_REPLY_REVIEW_PROMPT_VERSION,
            "claim_auditor_prompt_version": reply_strategy.CLAIM_AUDITOR_PROMPT_VERSION,
        }
        output_inventory = list(OUTPUT_FILES)
        run_manifest = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "case_pack_version": CASE_PACK_VERSION,
            "arguments": {
                "selection": str(selection),
                "shortlist": str(shortlist),
                "history_corpus": (
                    str(Path(history_corpus).resolve()) if history_corpus is not None else None
                ),
                "snapshot_root": (
                    str(Path(snapshot_root).resolve()) if snapshot_root is not None else None
                ),
                "snapshot_project_relative_path": (
                    str(snapshot_project_relative_path)
                    if snapshot_project_relative_path is not None
                    else None
                ),
                "output": str(output),
                "created_at": created_at,
                "recent_reply_limit": recent_reply_limit,
                "calibration_per_stratum": calibration_per_stratum,
            },
            "created_at": created_at,
            "current_git_commit": git_commit(),
            "reply_strategy_sha256": sha256_file(WORKTREE / "reply_strategy.py"),
            "current_versions": current_prompt_versions,
            "selection_directory": str(selection),
            "selection_input_sha256": selection_hashes,
            "shortlist_directory": str(shortlist),
            "shortlist_input_sha256": shortlist_hashes,
            "source_extractor_commit": shortlist_manifest["source_extractor_commit"],
            "source_evaluation_pool_tool_commit": shortlist_manifest["running_tool_git_commit"],
            "selected_count": 48,
            "replay_ready_count": 48,
            "selected_quote_tweet_count": len(selected_quote_ids),
            "quote_contexts_present_in_source_candidates": quote_contexts_present_in_source,
            "quote_contexts_recovered_from_snapshot_cache": len(recovery_rows),
            "quote_context_recovery_failures": 0,
            "quote_context_conflicts": 0,
            "snapshots_consulted": len(recovery_bundle["selected_snapshots"]),
            "snapshot_bot_state_files_opened": len(
                recovery_bundle["snapshot_bot_state_files"]
            ),
            "snapshot_bot_state_files_hashed": len(
                recovery_bundle["snapshot_bot_state_files"]
            ),
            "counts_by_final_stratum": counts_by_stratum,
            "counts_by_current_lane": lane_counts,
            "historical_outcome_counts": outcome_counts,
            "prompt_era_coverage": {"count": len(prompt_eras), "prompt_era_ids": prompt_eras},
            "recent_reply_limit": recent_reply_limit,
            "recent_reply_coverage": {"minimum": min(recent_counts), "maximum": max(recent_counts)},
            "calibration_count": len(calibration),
            "replay_plan_counts": {
                "calibration_pipeline_executions": len(calibration) * 2,
                "full_pipeline_executions": 96,
                "model_calls_performed": 0,
            },
            "output_inventory": output_inventory,
        }
        report = build_report(
            counts_by_stratum,
            lane_counts,
            outcome_counts,
            len(prompt_eras),
            min(recent_counts),
            max(recent_counts),
            len(selected_quote_ids),
            len(recovery_rows),
        )
        contents = {
            "run_manifest.json": json_bytes(run_manifest),
            "source_verification.json": json_bytes(source_verification),
            "frozen_cases.jsonl": jsonl_bytes(frozen),
            "model_inputs.jsonl": jsonl_bytes(model_inputs),
            "historical_baselines.jsonl": jsonl_bytes(baselines),
            "recent_account_replies.jsonl": jsonl_bytes(recent_outputs),
            "calibration_cases.jsonl": jsonl_bytes(calibration),
            "replay_plan.json": json_bytes(replay_plan),
            "case_pack_report.md": report.encode("utf-8"),
            "leakage_audit.json": json_bytes(leakage_audit),
            "quote_context_recovery.jsonl": jsonl_bytes(recovery_rows),
        }
        for name in OUTPUT_FILES:
            if name != "SHA256SUMS":
                write_private_file(output, name, contents[name])
        checksum_lines = [
            f"{sha256_file(output / name)}  {name}\n"
            for name in OUTPUT_FILES
            if name != "SHA256SUMS"
        ]
        write_private_file(output, "SHA256SUMS", "".join(checksum_lines).encode("utf-8"))
        return run_manifest
    except PackError as exc:
        if not any(output.iterdir()):
            diagnostic = {
                "schema_version": SCHEMA_VERSION,
                "tool_version": TOOL_VERSION,
                "status": "failed_without_frozen_pack",
                "error": exc.message,
                "candidate_fields": exc.candidate_fields,
            }
            write_private_file(output, "diagnostic_report.json", json_bytes(diagnostic))
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--shortlist", required=True, type=Path)
    parser.add_argument("--history-corpus", type=Path)
    parser.add_argument("--snapshot-root", type=Path)
    parser.add_argument("--snapshot-project-relative-path", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--recent-reply-limit", type=int, default=20)
    parser.add_argument("--calibration-per-stratum", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    args = build_parser().parse_args(argv)
    try:
        manifest = prepare_pack(
            selection=args.selection,
            shortlist=args.shortlist,
            output=args.output,
            created_at=args.created_at,
            recent_reply_limit=args.recent_reply_limit,
            calibration_per_stratum=args.calibration_per_stratum,
            history_corpus=args.history_corpus,
            snapshot_root=args.snapshot_root,
            snapshot_project_relative_path=args.snapshot_project_relative_path,
        )
    except PackError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        for candidate_id, fields in sorted(exc.candidate_fields.items()):
            print(f"{candidate_id}: {','.join(fields)}", file=sys.stderr)
        return 1
    summary = {
        "selected_count": manifest["selected_count"],
        "replay_ready_count": manifest["replay_ready_count"],
        "calibration_count": manifest["calibration_count"],
        "model_calls_performed": manifest["replay_plan_counts"]["model_calls_performed"],
        "output": manifest["arguments"]["output"],
    }
    print(stable_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
