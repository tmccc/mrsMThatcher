#!/usr/bin/env python3
"""
generate_all_openai_quote_images.py

Generate one OpenAI image for every UNIQUE analysed quote in
quote_analysis.json.

This is an offline experimental corpus. Nothing is added to the production
image pool and no production bot state is modified.

Native analysis identity:

    mrsMThatcher.txt physical line
        -> quote_analysis.json["line_index"][line]
        -> quote hash
        -> quote_analysis.json["items"][quote_hash]

Important:

    quote_analysis.json currently contains:
        633 line-index entries
        632 unique analysis items

    Therefore this script generates once per UNIQUE quote hash, not once per
    source line. Duplicate source lines share the same generated image.

Generation defaults:

    model:       gpt-image-1.5
    quality:     low
    size:        1024x1024
    images:      1 per unique quote hash

The prompt builder intentionally matches the earlier first-ten experiment:
it uses the existing quote analysis and does NOT include the original quote
text by default.

Default inputs:

    /disks/disk1/etc/mrsMThatcher/mrsMThatcher.txt
    /disks/disk1/etc/mrsMThatcher/quote_analysis.json

Default output:

    /disks/disk1/etc/mrsMThatcher/openai_generated_quote_images

New output directories are created with mode 0700. Existing unsafe budget
directories remain rejected. A --dry-run writes prompts and manifests without
initializing or changing the durable attempt-cost ledger or its lock.

Output layout:

    openai_generated_quote_images/
        run_manifest.json
        corpus_index.json
        items/
            230b8d71f541.../
                quote.txt
                quote_analysis.json
                quote_analysis_record.json
                generation_prompt.txt
                match_info.json
                image_01.png
                response_metadata.json
                manifest.json

Resume behaviour:

    * completed image items are skipped
    * interrupted/error items are retried on the next run
    * partial directories without image_01.* are safe to reuse
    * --force regenerates selected items
    * run_manifest.json is rewritten atomically after every item

Examples:

    # Validate the whole corpus and write prompts only.
    python3 generate_all_openai_quote_images.py --dry-run

    # Generate the first five unique quotes.
    python3 generate_all_openai_quote_images.py --max-new-images 5

    # Full run.
    python3 generate_all_openai_quote_images.py

    # Retry only items that do not yet have an image.
    python3 generate_all_openai_quote_images.py

    # Regenerate the selected corpus from scratch.
    python3 generate_all_openai_quote_images.py --force

Observed first-ten cost:

    approximately $0.13 for 10 images
    approximately $0.013 observed total cost per image request

The script uses that observed rate only for a conservative local estimate.
OpenAI billing remains the source of truth.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import base64
import html
import json
import os
import re
import secrets
import stat
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from decimal import Decimal

from provider_endpoint_policy import validate_provider_endpoint

import math
import requests


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

ROOT = Path("/disks/disk1/etc/mrsMThatcher")

DEFAULT_QUOTE_FILE = ROOT / "mrsMThatcher.txt"
DEFAULT_QUOTE_ANALYSIS = ROOT / "quote_analysis.json"

DEFAULT_OUTPUT_DIR = (
    ROOT / "openai_generated_quote_images"
)

DEFAULT_ENV_FILE = Path(
    "/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env"
)

OPENAI_IMAGES_URL = (
    "https://api.openai.com/v1/images/generations"
)

DEFAULT_MODEL = "gpt-image-1.5"
DEFAULT_QUALITY = "low"
DEFAULT_SIZE = "1024x1024"

# Published output-only price for gpt-image-1.5 low 1024x1024.
PUBLISHED_IMAGE_OUTPUT_COST_USD = 0.009

# Tony's actual observed first-ten total:
#     approximately $0.13 / 10 = $0.013 per request
#
# This includes the long prompt input and is therefore a better practical
# estimate for this exact experiment than output-only pricing.
DEFAULT_OBSERVED_ESTIMATED_COST_USD = 0.013

# Conservative safety ceiling for the full current corpus.
DEFAULT_MAX_ESTIMATED_COST_USD = 12.00

IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def log(message: str = "") -> None:
    """Write a timestamped generation progress message."""
    print(message, flush=True)


def utc_now_iso() -> str:
    """Return the UTC now iso."""
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(
    path: Path,
    value: Any,
) -> None:
    """Write JSON atomic."""
    temporary = path.with_name(
        path.name + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def load_env_file(path: Path) -> None:
    """
    Minimal .env reader.

    Existing process environment variables take precedence.
    """
    if not path.exists():
        return

    for raw_line in path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        os.environ.setdefault(
            key,
            value,
        )


# ---------------------------------------------------------------------------
# Quote identity and verification
# ---------------------------------------------------------------------------

def normalise_quote(text: str) -> str:
    """
    Normalise only typography and whitespace.

    This is used to verify that quote_analysis.json still corresponds to
    the current source file.
    """
    text = html.unescape(str(text))
    text = unicodedata.normalize(
        "NFKC",
        text,
    )

    text = text.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201a": "'",
                "\u201b": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u201e": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u00a0": " ",
            }
        )
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    text = text.strip(
        " \t\r\n\"'"
    )

    return text.casefold()


def load_source_lines(
    path: Path,
) -> dict[int, str]:
    """Load source lines."""
    if not path.exists():
        raise FileNotFoundError(
            f"Quote file does not exist: {path}"
        )

    result: dict[int, str] = {}

    for line_number, raw_line in enumerate(
        path.read_text(
            encoding="utf-8"
        ).splitlines(),
        start=1,
    ):
        text = raw_line.strip()

        if text:
            result[line_number] = text

    return result


# ---------------------------------------------------------------------------
# quote_analysis.json loading
# ---------------------------------------------------------------------------

def load_quote_analysis_store(
    path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    """Load quote analysis store."""
    if not path.exists():
        raise FileNotFoundError(
            f"Quote-analysis file does not exist: {path}"
        )

    data = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, dict):
        raise RuntimeError(
            "quote_analysis.json top level must be an object"
        )

    analysis_kind = data.get(
        "analysis_kind"
    )

    if analysis_kind not in {
        None,
        "quotes",
    }:
        raise RuntimeError(
            "Unexpected analysis_kind: "
            f"{analysis_kind!r}"
        )

    items = data.get("items")
    line_index = data.get("line_index")

    if not isinstance(items, dict):
        raise RuntimeError(
            "quote_analysis.json has no valid 'items' object"
        )

    if not isinstance(line_index, dict):
        raise RuntimeError(
            "quote_analysis.json has no valid 'line_index' object"
        )

    return data, items, line_index


def validate_store_against_source(
    *,
    source_lines: dict[int, str],
    items: dict[str, dict[str, Any]],
    line_index: dict[str, str],
) -> None:
    """
    Verify every indexed source line before any generation starts.
    """
    problems: list[str] = []

    for line_key, quote_hash in line_index.items():
        try:
            line_number = int(line_key)
        except (TypeError, ValueError):
            problems.append(
                f"Invalid line_index key: {line_key!r}"
            )
            continue

        if not isinstance(
            quote_hash,
            str,
        ) or not quote_hash:
            problems.append(
                f"Invalid hash for source line {line_number}"
            )
            continue

        source_text = source_lines.get(
            line_number
        )

        if source_text is None:
            problems.append(
                f"Indexed source line {line_number} "
                "is absent or empty"
            )
            continue

        record = items.get(
            quote_hash
        )

        if not isinstance(record, dict):
            problems.append(
                f"Line {line_number} maps to missing "
                f"item {quote_hash}"
            )
            continue

        stored_text = record.get("text")

        if not isinstance(
            stored_text,
            str,
        ) or not stored_text.strip():
            problems.append(
                f"Item {quote_hash} has no stored text"
            )
            continue

        if (
            normalise_quote(source_text)
            != normalise_quote(stored_text)
        ):
            problems.append(
                "\n".join(
                    [
                        f"Text mismatch on line {line_number}:",
                        f"  source: {source_text}",
                        f"  stored: {stored_text}",
                        f"  hash:   {quote_hash}",
                    ]
                )
            )

    if problems:
        preview = "\n\n".join(
            problems[:20]
        )

        more = (
            ""
            if len(problems) <= 20
            else (
                f"\n\n... and "
                f"{len(problems) - 20} more problem(s)"
            )
        )

        raise RuntimeError(
            "Quote-analysis/source validation failed:\n\n"
            + preview
            + more
        )


# ---------------------------------------------------------------------------
# Corpus ordering
# ---------------------------------------------------------------------------

def valid_line_numbers(
    record: dict[str, Any],
) -> list[int]:
    """Return whether valid line numbers."""
    raw = record.get(
        "line_numbers",
        [],
    )

    if not isinstance(raw, list):
        return []

    result: list[int] = []

    for value in raw:
        if isinstance(value, bool):
            continue

        try:
            number = int(value)
        except (TypeError, ValueError):
            continue

        if number > 0:
            result.append(number)

    return sorted(set(result))


def build_unique_corpus(
    items: dict[str, dict[str, Any]],
) -> list[
    tuple[
        str,
        dict[str, Any],
    ]
]:
    """
    One generation unit per unique quote hash.

    Sort primarily by earliest source line, then hash.
    """
    corpus: list[
        tuple[
            str,
            dict[str, Any],
        ]
    ] = []

    for quote_hash, record in items.items():
        if not isinstance(
            quote_hash,
            str,
        ) or not quote_hash:
            raise RuntimeError(
                f"Invalid quote hash key: {quote_hash!r}"
            )

        if not isinstance(record, dict):
            raise RuntimeError(
                f"Item {quote_hash} is not an object"
            )

        analysis = record.get(
            "analysis"
        )

        if not isinstance(
            analysis,
            dict,
        ):
            raise RuntimeError(
                f"Item {quote_hash} has no valid analysis"
            )

        text = record.get(
            "text"
        )

        if not isinstance(
            text,
            str,
        ) or not text.strip():
            raise RuntimeError(
                f"Item {quote_hash} has no valid quote text"
            )

        corpus.append(
            (
                quote_hash,
                record,
            )
        )

    def sort_key(
        item: tuple[
            str,
            dict[str, Any],
        ],
    ) -> tuple[int, str]:
        quote_hash, record = item

        lines = valid_line_numbers(
            record
        )

        first_line = (
            lines[0]
            if lines
            else 10**12
        )

        return (
            first_line,
            quote_hash,
        )

    corpus.sort(
        key=sort_key
    )

    return corpus


# ---------------------------------------------------------------------------
# Prompt construction
#
# This deliberately matches the first-ten experiment.
# ---------------------------------------------------------------------------

def clean_scalar(value: Any) -> str:
    """Return the clean scalar."""
    if value is None:
        return ""

    if isinstance(value, str):
        return re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

    if isinstance(
        value,
        (int, float, bool),
    ):
        return str(value)

    return ""


def string_list(value: Any) -> list[str]:
    """Return the string list."""
    if not isinstance(value, list):
        return []

    result: list[str] = []

    for item in value:
        text = clean_scalar(item)

        if text:
            result.append(text)

    return result


def labelled_list(
    label: str,
    values: list[str],
) -> str:
    """Return the labelled list."""
    if not values:
        return ""

    return (
        f"{label}:\n"
        + "\n".join(
            f"- {value}"
            for value in values
        )
    )


def historical_context_text(
    historical: Any,
) -> str:
    """Return the historical context text."""
    if not isinstance(
        historical,
        dict,
    ):
        return ""

    parts: list[str] = []

    explanation = clean_scalar(
        historical.get(
            "explanation"
        )
    )

    specificity = clean_scalar(
        historical.get(
            "specificity"
        )
    )

    needs_match = historical.get(
        "needs_historical_image_match"
    )

    if explanation:
        parts.append(explanation)

    if specificity:
        parts.append(
            f"Historical specificity: "
            f"{specificity}."
        )

    if isinstance(
        needs_match,
        bool,
    ):
        parts.append(
            (
                "A specific historical image match "
                "is required."
            )
            if needs_match
            else (
                "No exact historical event recreation "
                "is required."
            )
        )

    for key, label in (
        (
            "referenced_events",
            "Referenced events",
        ),
        (
            "referenced_people",
            "Referenced people",
        ),
        (
            "referenced_places",
            "Referenced places",
        ),
    ):
        values = string_list(
            historical.get(key)
        )

        if values:
            parts.append(
                f"{label}: "
                f"{', '.join(values)}."
            )

    return "\n".join(parts)


def archive_guidance_sections(
    archive_preferences: Any,
) -> list[str]:
    """
    Convert existing archive-matcher guidance into general generation
    guidance, matching the first-ten experiment.
    """
    if not isinstance(
        archive_preferences,
        dict,
    ):
        return []

    sections: list[str] = []

    matching_summary = clean_scalar(
        archive_preferences.get(
            "matching_summary"
        )
    )

    if matching_summary:
        sections.append(
            "Overall visual direction:\n"
            + matching_summary
        )

    mappings = (
        (
            "preferred_scenes",
            "Preferred scene types",
        ),
        (
            "preferred_activities",
            "Preferred activities",
        ),
        (
            "preferred_subject_moods",
            "Preferred subject moods",
        ),
        (
            "preferred_visible_symbols",
            "Useful visible symbols",
        ),
        (
            "visual_affinities",
            "Visual affinities",
        ),
    )

    for key, label in mappings:
        values = string_list(
            archive_preferences.get(key)
        )

        section = labelled_list(
            label,
            values,
        )

        if section:
            sections.append(section)

    strong_mismatches = string_list(
        archive_preferences.get(
            "strong_visual_mismatches"
        )
    )

    weak_mismatches = string_list(
        archive_preferences.get(
            "weak_visual_mismatches"
        )
    )

    avoid = (
        strong_mismatches
        + weak_mismatches
    )

    if avoid:
        sections.append(
            labelled_list(
                "Visual ideas to avoid",
                avoid,
            )
        )

    return sections


def build_generation_prompt(
    analysis: dict[str, Any],
    *,
    quote: str,
    include_quote: bool,
    max_chars: int,
) -> tuple[
    str,
    list[str],
]:
    """
    Match the first-ten generation prompt builder.

    By default the original quotation itself is NOT included.
    """
    sections: list[str] = []
    used_fields: list[str] = []

    intro = (
        "Create one visually striking editorial image to accompany a "
        "Margaret Thatcher quotation on X.\n\n"
        "Use the existing Grok analysis below as the semantic and visual "
        "brief. Create a single coherent scene or composition that "
        "expresses the analysed idea immediately at social-media size. "
        "Prefer one clear, specific visual idea over generic political "
        "imagery or an unfocused collage.\n\n"
        "The image may depict Margaret Thatcher when the visual brief "
        "naturally calls for a political leader, stateswoman, portrait, "
        "speech or recognisable public figure, but do not force her into "
        "every composition."
    )

    if include_quote:
        sections.append(
            "Original quotation:\n"
            + quote
        )
        used_fields.append(
            "original_quote"
        )

    summary = clean_scalar(
        analysis.get("summary")
    )

    if summary:
        sections.append(
            "Meaning to express:\n"
            + summary
        )
        used_fields.append(
            "summary"
        )

    concepts = string_list(
        analysis.get(
            "literal_visual_concepts"
        )
    )

    if concepts:
        sections.append(
            labelled_list(
                "Concrete visual concepts",
                concepts,
            )
        )
        used_fields.append(
            "literal_visual_concepts"
        )

    primary_topics = string_list(
        analysis.get(
            "primary_topics"
        )
    )

    if primary_topics:
        sections.append(
            labelled_list(
                "Primary themes",
                primary_topics,
            )
        )
        used_fields.append(
            "primary_topics"
        )

    secondary_topics = string_list(
        analysis.get(
            "secondary_topics"
        )
    )

    if secondary_topics:
        sections.append(
            labelled_list(
                "Secondary themes",
                secondary_topics,
            )
        )
        used_fields.append(
            "secondary_topics"
        )

    keywords = string_list(
        analysis.get(
            "specific_keywords"
        )
    )

    if keywords:
        sections.append(
            labelled_list(
                "Specific concepts and symbols",
                keywords,
            )
        )
        used_fields.append(
            "specific_keywords"
        )

    tone = string_list(
        analysis.get("tone")
    )

    if tone:
        sections.append(
            labelled_list(
                "Emotional tone",
                tone,
            )
        )
        used_fields.append(
            "tone"
        )

    visual_energy = clean_scalar(
        analysis.get(
            "visual_energy"
        )
    )

    if visual_energy:
        sections.append(
            "Visual energy:\n"
            + visual_energy
        )
        used_fields.append(
            "visual_energy"
        )

    emotional_intensity = analysis.get(
        "emotional_intensity"
    )

    if isinstance(
        emotional_intensity,
        (int, float),
    ):
        sections.append(
            "Emotional intensity:\n"
            f"{emotional_intensity}/100"
        )
        used_fields.append(
            "emotional_intensity"
        )

    historical = historical_context_text(
        analysis.get(
            "historical_context"
        )
    )

    if historical:
        sections.append(
            "Historical guidance:\n"
            + historical
        )
        used_fields.append(
            "historical_context"
        )

    archive_sections = archive_guidance_sections(
        analysis.get(
            "archive_image_preferences"
        )
    )

    if archive_sections:
        sections.extend(
            archive_sections
        )
        used_fields.append(
            "archive_image_preferences"
        )

    constraints = (
        "Generation constraints:\n"
        "- Do not include any visible words, quotation text, captions, "
        "subtitles, labels, speech bubbles, logos or watermarks.\n"
        "- Do not create a quote card or poster.\n"
        "- Avoid readable text on signs, documents or screens.\n"
        "- Keep the composition visually legible at social-media size.\n"
        "- Prefer a purposeful editorial image over generic stock "
        "photography.\n"
        "- Use realistic or polished editorial visual language unless "
        "the analysis clearly supports something more symbolic."
    )

    full_prompt = (
        intro
        + "\n\n"
        + "\n\n".join(sections)
        + "\n\n"
        + constraints
    ).strip()

    if len(full_prompt) > max_chars:
        raise RuntimeError(
            f"Generated prompt is "
            f"{len(full_prompt)} characters, "
            f"exceeding max {max_chars}"
        )

    if not used_fields:
        raise RuntimeError(
            "Analysis contains none of the expected "
            "prompt fields"
        )

    return (
        full_prompt,
        used_fields,
    )


# ---------------------------------------------------------------------------
# OpenAI request
# ---------------------------------------------------------------------------

def retry_delay(
    response: requests.Response | None,
    attempt: int,
) -> float:
    """Retry delay."""
    if response is not None:
        retry_after = response.headers.get(
            "Retry-After"
        )

        if retry_after:
            try:
                value = float(
                    retry_after
                )

                if value >= 0:
                    return min(
                        value,
                        300.0,
                    )
            except ValueError:
                pass

    return float(
        min(
            120,
            2 ** (attempt + 1),
        )
    )


class AttemptBudget:
    """Serialize durable request-cost reservations across processes and instances."""

    def __init__(self, path: Path, *, per_request: float, ceiling: float):
        """Recover private exposure under its permanent, owned singleton lock."""
        self.path = Path(path)
        self.lock_name = self.path.name + ".lock"
        self.per_request = Decimal(str(per_request))
        self.ceiling = Decimal(str(ceiling))
        if not self.per_request.is_finite() or not self.ceiling.is_finite() or self.per_request <= 0 or self.ceiling < 0:
            raise ValueError("attempt cost must be positive and ceiling finite/non-negative")
        with self._locked() as (directory_fd, lock_fd, created):
            entry, count = self._read(directory_fd)
            if entry is None:
                if not created:
                    raise RuntimeError("attempt-cost ledger missing beside existing budget lock")
                self._publish(directory_fd, lock_fd, entry, count)
            self.attempts = count

    @contextmanager
    def _locked(self):
        """Hold flock on one no-follow, single-link 0600 file in an owned directory."""
        from exact_receipt_retirement import _open_directory

        directory_fd = _open_directory(self.path.parent)
        lock_fd = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            try:
                lock_fd = os.open(self.lock_name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
                created = True
            except FileExistsError:
                lock_fd = os.open(self.lock_name, flags, dir_fd=directory_fd)
                created = False
            self._require_lock(directory_fd, lock_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._require_lock(directory_fd, lock_fd)
            if created:
                os.fsync(lock_fd)
                os.fsync(directory_fd)
            yield directory_fd, lock_fd, created
        finally:
            # Closing releases flock without ever unlinking its durable namespace.
            if lock_fd is not None:
                os.close(lock_fd)
            os.close(directory_fd)

    def _require_lock(self, directory_fd: int, lock_fd: int) -> None:
        """Reject replaced lock or directory identities before a reservation commits."""
        from exact_receipt_retirement import FileIdentity, _open_directory, _read_stable_entry

        current_directory = _open_directory(self.path.parent)
        try:
            before, current = os.fstat(directory_fd), os.fstat(current_directory)
            if (before.st_dev, before.st_ino, before.st_uid, before.st_mode) != (
                    current.st_dev, current.st_ino, current.st_uid, current.st_mode):
                raise RuntimeError("attempt-budget directory identity changed")
        finally:
            os.close(current_directory)
        metadata = os.fstat(lock_fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1 or metadata.st_size != 0):
            raise RuntimeError("unsafe attempt-budget lock authority")
        current_lock = _read_stable_entry(directory_fd, self.lock_name, maximum=0)
        if current_lock is None or current_lock.identity != FileIdentity.from_stat(metadata):
            raise RuntimeError("attempt-budget lock identity changed")

    def _read(self, directory_fd: int):
        """Read the actual bounded exposure; cached instance counts never authorize calls."""
        from exact_receipt_retirement import _read_stable_entry, _strict_object

        entry = _read_stable_entry(directory_fd, self.path.name, maximum=4096)
        if entry is None:
            return None, 0
        if entry.identity.mode != 0o600:
            raise RuntimeError("attempt-cost ledger must have mode 0600")
        prior = _strict_object(entry.data, label="attempt-cost ledger")
        if set(prior) != {"attempted_requests", "reserved_estimated_cost_usd", "max_estimated_cost_usd"}:
            raise ValueError("invalid persisted request exposure fields")
        count = prior["attempted_requests"]
        if type(count) is not int or count < 0:
            raise ValueError("invalid persisted request exposure")
        exposure = Decimal(prior["reserved_estimated_cost_usd"])
        prior_ceiling = Decimal(prior["max_estimated_cost_usd"])
        if not exposure.is_finite() or exposure != self.per_request * count:
            raise ValueError("persisted request cost estimate changed")
        if not prior_ceiling.is_finite() or prior_ceiling < 0 or exposure > prior_ceiling:
            raise ValueError("invalid persisted request ceiling")
        # Another operation may tighten the shared ceiling; a stale caller cannot
        # silently increase it again. Raising it requires explicit ledger recovery.
        self.ceiling = min(self.ceiling, prior_ceiling)
        return entry, count

    def _publish(self, directory_fd: int, lock_fd: int, prior, count: int) -> None:
        """Fsync a unique private staging file, replace under lock, and prove publication."""
        from exact_receipt_retirement import _read_stable_entry, _same_object_after_rename, _stage_new, _unlink_exact_cleanup

        data = (json.dumps({
            "attempted_requests": count,
            "reserved_estimated_cost_usd": str(self.per_request * count),
            "max_estimated_cost_usd": str(self.ceiling),
        }, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        temporary = "." + self.path.name + "." + secrets.token_hex(16) + ".tmp"
        self._require_lock(directory_fd, lock_fd)
        staged = _stage_new(directory_fd, temporary, data)
        replaced = False
        try:
            self._require_lock(directory_fd, lock_fd)
            if _read_stable_entry(directory_fd, self.path.name, maximum=4096) != prior:
                raise RuntimeError("attempt-cost ledger changed before reservation")
            os.replace(temporary, self.path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            replaced = True
            os.fsync(directory_fd)
            self._require_lock(directory_fd, lock_fd)
            committed = _read_stable_entry(directory_fd, self.path.name, maximum=4096)
            if (committed is None or committed.data != data or committed.identity.mode != 0o600
                    or not _same_object_after_rename(staged.identity, committed.identity)):
                raise RuntimeError("attempt-cost reservation changed during publication")
        finally:
            if not replaced:
                _unlink_exact_cleanup(directory_fd, cleanup_name=temporary,
                                      expected_entry=staged, maximum=4096)

    def reserve(self) -> None:
        """Reload and durably reserve before a request; every failed attempt remains counted."""
        with self._locked() as (directory_fd, lock_fd, _created):
            entry, count = self._read(directory_fd)
            if entry is None:
                raise RuntimeError("attempt-cost ledger disappeared before reservation")
            self.attempts = count
            next_count = count + 1
            if self.per_request * next_count > self.ceiling:
                raise RuntimeError("attempted-request cost ceiling reached")
            self._publish(directory_fd, lock_fd, entry, next_count)
            self.attempts = next_count


def request_image(
    session: requests.Session,
    *,
    api_key: str,
    model: str,
    prompt: str,
    quality: str,
    size: str,
    max_retries: int,
    reserve_attempt: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Return the request image."""
    validate_provider_endpoint(OPENAI_IMAGES_URL, provider="openai", test_mode=os.getenv("MRS_TEST_MODE") == "1")
    payload = {
        "model": model,
        "prompt": prompt,
        "quality": quality,
        "size": size,
        "n": 1,
        "output_format": "png",
    }

    for attempt in range(
        max_retries + 1
    ):
        response: requests.Response | None = None

        if reserve_attempt is not None:
            reserve_attempt()
        try:
            response = session.post(
                OPENAI_IMAGES_URL,
                headers={
                    "Authorization": (
                        f"Bearer {api_key}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                },
                json=payload,
                timeout=(30, 600),
                allow_redirects=False,
            )

        except requests.RequestException as exc:
            # Even a connection reset may follow acceptance by the provider.
            # No supported idempotency contract exists for this image endpoint.
            raise RuntimeError("OpenAI image request outcome is ambiguous; automatic retry refused") from exc

        if 200 <= response.status_code < 300:
            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "OpenAI response was not valid JSON:\n"
                    + response.text[:2000]
                ) from exc

            if not isinstance(
                data,
                dict,
            ):
                raise RuntimeError(
                    "Unexpected OpenAI response type: "
                    f"{type(data).__name__}"
                )

            return data

        retryable = response.status_code == 429

        if (
            retryable
            and attempt < max_retries
        ):
            delay = retry_delay(
                response,
                attempt,
            )

            log(
                f"    OpenAI HTTP "
                f"{response.status_code}"
            )
            log(
                f"    retrying in {delay:.1f}s"
            )

            time.sleep(delay)
            continue

        raise RuntimeError(
            "\n".join(
                [
                    "OpenAI image generation failed.",
                    (
                        f"HTTP status: "
                        f"{response.status_code}"
                    ),
                    "Response:",
                    response.text[:4000],
                ]
            )
        )

    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Image response handling
# ---------------------------------------------------------------------------

def decode_image_response(
    response_data: dict[str, Any],
) -> tuple[
    bytes,
    dict[str, Any],
]:
    """Decode image response."""
    data = response_data.get("data")

    if not isinstance(data, list):
        raise RuntimeError(
            "OpenAI response has no valid data list"
        )

    if len(data) != 1:
        raise RuntimeError(
            f"Expected exactly one image item, "
            f"received {len(data)}"
        )

    item = data[0]

    if not isinstance(item, dict):
        raise RuntimeError(
            f"Unexpected image item: {item!r}"
        )

    encoded = item.get(
        "b64_json"
    )

    if not isinstance(
        encoded,
        str,
    ) or not encoded:
        raise RuntimeError(
            "Image response contains no b64_json"
        )

    try:
        image_bytes = base64.b64decode(
            encoded,
            validate=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "Could not decode b64_json image"
        ) from exc

    image_info = {
        "revised_prompt": item.get(
            "revised_prompt",
            "",
        ),
    }

    return (
        image_bytes,
        image_info,
    )


def response_metadata_only(
    response_data: dict[str, Any],
) -> dict[str, Any]:
    """Return the response metadata only."""
    stored = json.loads(
        json.dumps(response_data)
    )

    data = stored.get("data")

    if isinstance(data, list):
        for item in data:
            if (
                isinstance(item, dict)
                and "b64_json" in item
            ):
                item["b64_json"] = (
                    "[base64 image omitted; "
                    "decoded image saved separately]"
                )

    return stored


def find_existing_image(
    item_dir: Path,
) -> Path | None:
    """Find existing image."""
    for suffix in sorted(
        IMAGE_SUFFIXES
    ):
        candidate = (
            item_dir
            / f"image_01{suffix}"
        )

        if candidate.exists():
            return candidate

    return None


# ---------------------------------------------------------------------------
# Corpus index
# ---------------------------------------------------------------------------

def build_corpus_index(
    corpus: list[
        tuple[
            str,
            dict[str, Any],
        ]
    ],
) -> dict[str, Any]:
    """Build corpus index."""
    entries: list[
        dict[str, Any]
    ] = []

    line_to_hash: dict[
        str,
        str,
    ] = {}

    for ordinal, (
        quote_hash,
        record,
    ) in enumerate(
        corpus,
        start=1,
    ):
        lines = valid_line_numbers(
            record
        )

        for line_number in lines:
            line_to_hash[
                str(line_number)
            ] = quote_hash

        entries.append(
            {
                "corpus_ordinal": ordinal,
                "quote_hash": quote_hash,
                "line_numbers": lines,
                "text": record.get(
                    "text",
                    "",
                ),
                "item_directory": (
                    f"items/{quote_hash}"
                ),
            }
        )

    return {
        "created_at_utc": utc_now_iso(),
        "unique_quote_count": len(corpus),
        "entries": entries,
        "line_to_hash": line_to_hash,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate one OpenAI image per unique "
            "quote-analysis hash"
        )
    )

    parser.add_argument(
        "--quote-file",
        type=Path,
        default=DEFAULT_QUOTE_FILE,
    )

    parser.add_argument(
        "--quote-analysis",
        type=Path,
        default=DEFAULT_QUOTE_ANALYSIS,
    )

    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )

    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
    )

    parser.add_argument(
        "--quality",
        choices=(
            "low",
            "medium",
            "high",
        ),
        default=DEFAULT_QUALITY,
    )

    parser.add_argument(
        "--size",
        choices=(
            "1024x1024",
            "1024x1536",
            "1536x1024",
        ),
        default=DEFAULT_SIZE,
    )

    parser.add_argument(
        "--start",
        type=int,
        default=1,
        help=(
            "First unique corpus ordinal to consider "
            "(default: 1)"
        ),
    )

    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help=(
            "Number of unique corpus items to consider. "
            "Default: all remaining items."
        ),
    )

    parser.add_argument(
        "--max-new-images",
        type=int,
        default=None,
        help=(
            "Stop after generating this many NEW images. "
            "Already-complete items do not count."
        ),
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=15.0,
        help=(
            "Pause after each successful generation "
            "(default: 1 second)"
        ),
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--max-prompt-chars",
        type=int,
        default=6000,
    )

    parser.add_argument(
        "--estimated-cost-per-image",
        type=float,
        default=DEFAULT_OBSERVED_ESTIMATED_COST_USD,
        help=(
            "Local cost estimate only. "
            "Default reflects Tony's observed first-ten total."
        ),
    )

    parser.add_argument(
        "--max-estimated-cost",
        type=float,
        default=DEFAULT_MAX_ESTIMATED_COST_USD,
        help=(
            "Refuse to start if estimated cost of all "
            "currently missing selected images exceeds "
            "this amount. Default: $12."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate, write prompts and manifests, "
            "but make no API calls or initialize/change the cost ledger"
        ),
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Regenerate selected images even when "
            "image_01 already exists"
        ),
    )

    parser.add_argument(
        "--include-quote",
        action="store_true",
        help=(
            "Include the original quotation in the prompt. "
            "Default remains analysis-only, matching the "
            "first-ten experiment."
        ),
    )

    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help=(
            "Stop at the first failed item. "
            "Default is to record the error and continue."
        ),
    )

    args = parser.parse_args()

    if args.start < 1:
        parser.error(
            "--start must be at least 1"
        )

    if (
        args.count is not None
        and args.count < 1
    ):
        parser.error(
            "--count must be at least 1"
        )

    if (
        args.max_new_images is not None
        and args.max_new_images < 1
    ):
        parser.error(
            "--max-new-images must be at least 1"
        )

    if args.max_retries < 0:
        parser.error(
            "--max-retries cannot be negative"
        )

    if args.sleep < 0:
        parser.error(
            "--sleep cannot be negative"
        )

    if args.max_prompt_chars < 500:
        parser.error(
            "--max-prompt-chars must be at least 500"
        )

    if not math.isfinite(args.estimated_cost_per_image) or args.estimated_cost_per_image <= 0:
        parser.error(
            "--estimated-cost-per-image must be finite and positive"
        )

    if not math.isfinite(args.max_estimated_cost) or args.max_estimated_cost < 0:
        parser.error(
            "--max-estimated-cost must be finite and non-negative"
        )

    load_env_file(
        args.env_file
    )

    api_key = os.environ.get(
        "OPENAI_API_KEY",
        "",
    ).strip()

    if (
        not args.dry_run
        and not api_key
    ):
        print(
            "ERROR: OPENAI_API_KEY is not set and "
            f"was not found in {args.env_file}",
            file=sys.stderr,
        )
        return 2

    try:
        source_lines = load_source_lines(
            args.quote_file
        )

        (
            store,
            analysis_items,
            line_index,
        ) = load_quote_analysis_store(
            args.quote_analysis
        )

        validate_store_against_source(
            source_lines=source_lines,
            items=analysis_items,
            line_index=line_index,
        )

        corpus = build_unique_corpus(
            analysis_items
        )

    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 2

    begin = args.start - 1

    if args.count is None:
        selected = corpus[begin:]
    else:
        selected = corpus[
            begin : begin + args.count
        ]

    if not selected:
        print(
            "ERROR: selection is empty",
            file=sys.stderr,
        )
        return 2

    args.out.mkdir(
        mode=0o700,
        parents=True,
        exist_ok=True,
    )

    items_root = (
        args.out / "items"
    )

    items_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    corpus_index = build_corpus_index(
        corpus
    )

    write_json_atomic(
        args.out / "corpus_index.json",
        corpus_index,
    )

    # ---------------------------------------------------------------------
    # Pre-build prompts and calculate the number of requests that would
    # actually be made before spending anything.
    # ---------------------------------------------------------------------

    work_items: list[
        dict[str, Any]
    ] = []

    missing_selected = 0

    for corpus_offset, (
        quote_hash,
        record,
    ) in enumerate(
        selected,
        start=args.start,
    ):
        analysis = record.get(
            "analysis"
        )

        assert isinstance(
            analysis,
            dict,
        )

        quote = str(
            record.get(
                "text",
                "",
            )
        )

        prompt, used_fields = (
            build_generation_prompt(
                analysis,
                quote=quote,
                include_quote=args.include_quote,
                max_chars=args.max_prompt_chars,
            )
        )

        item_dir = (
            items_root / quote_hash
        )

        existing_image = find_existing_image(
            item_dir
        )

        needs_generation = (
            args.force
            or existing_image is None
        )

        if needs_generation:
            missing_selected += 1

        work_items.append(
            {
                "corpus_ordinal": corpus_offset,
                "quote_hash": quote_hash,
                "record": record,
                "analysis": analysis,
                "quote": quote,
                "prompt": prompt,
                "used_fields": used_fields,
                "item_dir": item_dir,
                "existing_image": existing_image,
                "needs_generation": needs_generation,
            }
        )

    requests_for_estimate = (
        missing_selected
    )

    if (
        args.max_new_images is not None
    ):
        requests_for_estimate = min(
            requests_for_estimate,
            args.max_new_images,
        )

    estimated_cost = (
        requests_for_estimate
        * args.estimated_cost_per_image
    )

    published_output_only_cost = (
        requests_for_estimate
        * PUBLISHED_IMAGE_OUTPUT_COST_USD
    )

    log(
        f"Quotes:                {args.quote_file}"
    )
    log(
        f"Quote analysis:        {args.quote_analysis}"
    )
    log(
        f"Source non-empty lines:{len(source_lines):>5}"
    )
    log(
        f"Line index entries:    {len(line_index):>5}"
    )
    log(
        f"Unique analysis items: {len(corpus):>5}"
    )
    log(
        f"Selected unique items: {len(work_items):>5}"
    )
    log(
        f"Already complete:      "
        f"{len(work_items) - missing_selected:>5}"
    )
    log(
        f"Missing selected:      {missing_selected:>5}"
    )
    log(
        f"New requests this run: "
        f"{requests_for_estimate:>5}"
    )
    log(
        f"Output:                {args.out}"
    )
    log(
        f"Model:                 {args.model}"
    )
    log(
        f"Quality:               {args.quality}"
    )
    log(
        f"Size:                  {args.size}"
    )
    log(
        f"Include quote:         {args.include_quote}"
    )
    log(
        f"Dry run:               {args.dry_run}"
    )
    log()
    log(
        "Published output-only estimate: "
        f"${published_output_only_cost:.2f}"
    )
    log(
        "Observed practical estimate:   "
        f"${estimated_cost:.2f}"
    )
    log(
        "Configured cost safety ceiling:"
        f" ${args.max_estimated_cost:.2f}"
    )
    log()

    if (
        not args.dry_run
        and estimated_cost
        > args.max_estimated_cost
    ):
        print(
            "ERROR: estimated cost exceeds the configured "
            "safety ceiling.\n"
            f"Estimated: ${estimated_cost:.2f}\n"
            f"Ceiling:   ${args.max_estimated_cost:.2f}\n\n"
            "Increase --max-estimated-cost explicitly only "
            "after checking the selection.",
            file=sys.stderr,
        )
        return 2

    # ---------------------------------------------------------------------
    # Run manifest
    # ---------------------------------------------------------------------

    run_manifest: dict[str, Any] = {
        "started_at_utc": utc_now_iso(),
        "quote_file": str(
            args.quote_file
        ),
        "quote_analysis_file": str(
            args.quote_analysis
        ),
        "quote_analysis_schema_version": (
            store.get(
                "schema_version"
            )
        ),
        "quote_analysis_prompt_version": (
            store.get(
                "prompt_version"
            )
        ),
        "output_directory": str(
            args.out
        ),
        "model": args.model,
        "quality": args.quality,
        "size": args.size,
        "include_quote_in_prompt": (
            args.include_quote
        ),
        "selection": {
            "start": args.start,
            "count": args.count,
            "selected_unique_items": (
                len(work_items)
            ),
            "missing_selected_at_start": (
                missing_selected
            ),
            "max_new_images": (
                args.max_new_images
            ),
        },
        "cost_estimate": {
            "published_output_only_usd_per_image": (
                PUBLISHED_IMAGE_OUTPUT_COST_USD
            ),
            "observed_estimated_usd_per_image": (
                args.estimated_cost_per_image
            ),
            "estimated_new_requests": (
                requests_for_estimate
            ),
            "published_output_only_total_usd": (
                published_output_only_cost
            ),
            "observed_estimated_total_usd": (
                estimated_cost
            ),
            "configured_ceiling_usd": (
                args.max_estimated_cost
            ),
        },
        "dry_run": args.dry_run,
        "items": [],
        "summary": {
            "generated": 0,
            "already_complete": 0,
            "dry_run": 0,
            "errors": 0,
        },
    }

    write_json_atomic(
        args.out / "run_manifest.json",
        run_manifest,
    )

    if not args.dry_run:
        budget = AttemptBudget(
            args.out / "attempt_cost_reservations.json",
            per_request=args.estimated_cost_per_image,
            ceiling=args.max_estimated_cost,
        )
    session = requests.Session()
    session.trust_env = False

    session.headers.update(
        {
            "User-Agent": (
                "mrsMThatcher-"
                "full-openai-quote-image-corpus/1.0"
            )
        }
    )

    generated_this_run = 0

    # ---------------------------------------------------------------------
    # Process corpus
    # ---------------------------------------------------------------------

    for position, work in enumerate(
        work_items,
        start=1,
    ):
        quote_hash = str(
            work["quote_hash"]
        )

        record = work["record"]
        analysis = work["analysis"]
        quote = str(
            work["quote"]
        )
        prompt = str(
            work["prompt"]
        )
        used_fields = list(
            work["used_fields"]
        )
        item_dir = work["item_dir"]

        assert isinstance(
            item_dir,
            Path,
        )

        line_numbers = (
            valid_line_numbers(
                record
            )
        )

        log(
            f"[{position}/{len(work_items)}] "
            f"corpus #{work['corpus_ordinal']} "
            f"hash {quote_hash[:12]}..."
        )

        log(
            f"    line(s): {line_numbers}"
        )

        log(
            f"    {quote}"
        )

        item_manifest: dict[str, Any] = {
            "corpus_ordinal": (
                work[
                    "corpus_ordinal"
                ]
            ),
            "quote_hash": quote_hash,
            "line_numbers": (
                line_numbers
            ),
            "quote": quote,
            "status": "pending",
            "model": args.model,
            "quality": args.quality,
            "size": args.size,
            "prompt_characters": (
                len(prompt)
            ),
            "prompt_fields_used": (
                used_fields
            ),
        }

        try:
            item_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            (
                item_dir
                / "quote.txt"
            ).write_text(
                quote + "\n",
                encoding="utf-8",
            )

            write_json_atomic(
                item_dir
                / "quote_analysis.json",
                analysis,
            )

            write_json_atomic(
                item_dir
                / "quote_analysis_record.json",
                record,
            )

            (
                item_dir
                / "generation_prompt.txt"
            ).write_text(
                prompt + "\n",
                encoding="utf-8",
            )

            match_info = {
                "corpus_ordinal": (
                    work[
                        "corpus_ordinal"
                    ]
                ),
                "quote_hash": (
                    quote_hash
                ),
                "line_numbers": (
                    line_numbers
                ),
                "analysis_model": (
                    record.get(
                        "analysis_model",
                        "",
                    )
                ),
                "analysis_prompt_version": (
                    record.get(
                        "prompt_version",
                        "",
                    )
                ),
                "analysed_at": (
                    record.get(
                        "analysed_at",
                        "",
                    )
                ),
                "generation_prompt_characters": (
                    len(prompt)
                ),
                "generation_prompt_fields": (
                    used_fields
                ),
                "original_quote_in_prompt": (
                    args.include_quote
                ),
            }

            write_json_atomic(
                item_dir
                / "match_info.json",
                match_info,
            )

            existing_image = (
                find_existing_image(
                    item_dir
                )
            )

            if (
                existing_image is not None
                and not args.force
            ):
                item_manifest[
                    "status"
                ] = "already_complete"

                item_manifest[
                    "image"
                ] = {
                    "file": (
                        existing_image.name
                    ),
                    "bytes": (
                        existing_image.stat().st_size
                    ),
                }

                run_manifest[
                    "summary"
                ][
                    "already_complete"
                ] += 1

                run_manifest[
                    "items"
                ].append(
                    item_manifest
                )

                write_json_atomic(
                    args.out
                    / "run_manifest.json",
                    run_manifest,
                )

                log(
                    "    already complete; skipping"
                )
                log()
                continue

            if args.dry_run:
                item_manifest[
                    "status"
                ] = "dry_run"

                run_manifest[
                    "summary"
                ][
                    "dry_run"
                ] += 1

                run_manifest[
                    "items"
                ].append(
                    item_manifest
                )

                write_json_atomic(
                    args.out
                    / "run_manifest.json",
                    run_manifest,
                )

                log(
                    "    dry run: prompt written; "
                    "no API call"
                )
                log()
                continue

            if (
                args.max_new_images is not None
                and generated_this_run
                >= args.max_new_images
            ):
                log(
                    "Reached --max-new-images limit; "
                    "stopping cleanly."
                )
                break

            log(
                "    requesting OpenAI image..."
            )

            response_data = request_image(
                session,
                api_key=api_key,
                model=args.model,
                prompt=prompt,
                quality=args.quality,
                size=args.size,
                max_retries=args.max_retries,
                reserve_attempt=budget.reserve,
            )

            (
                image_bytes,
                image_response_info,
            ) = decode_image_response(
                response_data
            )

            image_path = (
                item_dir
                / "image_01.png"
            )

            image_path.write_bytes(
                image_bytes
            )

            write_json_atomic(
                item_dir
                / "response_metadata.json",
                response_metadata_only(
                    response_data
                ),
            )

            item_manifest[
                "status"
            ] = "generated"

            item_manifest[
                "generated_at_utc"
            ] = utc_now_iso()

            item_manifest[
                "image"
            ] = {
                "file": (
                    image_path.name
                ),
                "bytes": (
                    len(image_bytes)
                ),
                **image_response_info,
            }

            write_json_atomic(
                item_dir
                / "manifest.json",
                item_manifest,
            )

            generated_this_run += 1

            run_manifest[
                "summary"
            ][
                "generated"
            ] += 1

            run_manifest[
                "items"
            ].append(
                item_manifest
            )

            write_json_atomic(
                args.out
                / "run_manifest.json",
                run_manifest,
            )

            log(
                f"    saved {image_path.name} "
                f"({len(image_bytes):,} bytes)"
            )

            log(
                f"    generated this run: "
                f"{generated_this_run}"
            )

            log()

            if args.sleep > 0:
                time.sleep(
                    args.sleep
                )

        except Exception as exc:
            item_manifest[
                "status"
            ] = "error"

            item_manifest[
                "error"
            ] = str(exc)

            item_manifest[
                "failed_at_utc"
            ] = utc_now_iso()

            run_manifest[
                "summary"
            ][
                "errors"
            ] += 1

            run_manifest[
                "items"
            ].append(
                item_manifest
            )

            write_json_atomic(
                args.out
                / "run_manifest.json",
                run_manifest,
            )

            log(
                f"    ERROR: {exc}"
            )

            log()

            if args.stop_on_error:
                run_manifest[
                    "stopped_because"
                ] = "error"

                break

    run_manifest[
        "finished_at_utc"
    ] = utc_now_iso()

    run_manifest[
        "generated_this_run"
    ] = generated_this_run

    write_json_atomic(
        args.out / "run_manifest.json",
        run_manifest,
    )

    log("Done.")
    log(
        f"Output: {args.out}"
    )
    log(
        f"Generated this run: {generated_this_run}"
    )
    log(
        "Actual billing should be checked in "
        "the OpenAI usage dashboard."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
