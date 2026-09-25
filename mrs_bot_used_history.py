"""Durable quote and image used history.

Each root invocation binds current paths, file authorities, AssetMetadata and
QuoteCandidates to UsedHistory. Load/save and source-verified migrations call
owned operations directly. Pure coercion/sorting and fixed JSON, hashing, regex
and basename transforms stay local. The shared image-normalization leaf also
supports the historical simulator's deliberately different corpus proof.
Numeric image indices require both complete metadata and unchanged original-glob
ordering; expanded PNG discovery cannot silently remap them.
No caller history is retained and import performs no runtime work.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from logging import Logger
from typing import Any

from mrs_bot_asset_metadata import AssetMetadata
from mrs_bot_quote_candidates import QuoteCandidates


def coerce_used_set(value: object, *, path: Path) -> set:
    """Normalise persisted used-history data to a set."""
    if isinstance(value, set):
        return value
    if isinstance(value, list):
        return set(value)

    raise ValueError(f"Used-history file {path} must contain a JSON list")


def used_set_to_sorted_list(value: set) -> list:
    """Return deterministic JSON-safe used-history values."""
    def sort_key(item: object) -> tuple[int, int | str]:
        try:
            return (0, int(item))
        except Exception:
            return (1, str(item))

    return sorted(value, key=sort_key)


@dataclass(frozen=True)
class UsedHistory:
    """Own history migration and rewrites through candidate and metadata owners."""

    corrupt_error: type[Exception]
    unsafe_namespace: type[Exception]
    log: Logger
    read_stable_bytes: Callable[..., tuple[bool, bytes | None]]
    write_json: Callable
    quote_candidates: QuoteCandidates
    metadata: AssetMetadata
    quote_history_file: Path
    legacy_quote_file: Path
    image_history_file: Path
    legacy_image_file: Path

    def load_used_set(self, path: Path, *, legacy_pickle_path: Path | None = None) -> set:
        """Load a fail-closed durable used-history set."""
        self.log.debug("Loading used-history set from %s", path)

        try:
            present, data = self.read_stable_bytes(path)
            if not present or data is None:
                raise FileNotFoundError(path)
            value = json.loads(data.decode("utf-8"))
            converted = coerce_used_set(value, path=path)
            if isinstance(value, list) and value != used_set_to_sorted_list(converted):
                self.save_used_set(path, converted)
                self.log.info("Normalized used-history JSON ordering in %s", path)
            self.log.debug("Loaded %d entries from %s", len(converted), path)
            return converted
        except FileNotFoundError:
            self.log.warning("Used-history JSON file does not exist yet: %s", path)
        except (OSError, self.unsafe_namespace):
            self.log.exception("OS error loading existing used-history JSON file %s; refusing stale legacy fallback", path)
            raise self.corrupt_error(f"Existing used-history JSON is unreadable: {path}")
        except Exception:
            self.log.exception("Failed loading existing used-history JSON file %s; refusing stale legacy fallback", path)
            raise self.corrupt_error(f"Existing used-history JSON is corrupt or invalid: {path}")

        if legacy_pickle_path is not None and legacy_pickle_path.exists():
            self.log.critical(
                "Used-history JSON %s is missing but legacy pickle %s exists; refusing unsafe pickle fallback. "
                "Restore the JSON history or migrate manually from a trusted backup.",
                path,
                legacy_pickle_path,
            )
            raise self.corrupt_error(f"Used-history JSON missing while legacy pickle exists: {path}")
        return set()

    def save_used_set(self, path: Path, value: set, *, durable: bool = False) -> None:
        """Persist a used-history set atomically."""
        self.log.debug("Saving %d entries to used-history JSON %s", len(value), path)
        self.write_json(path, used_set_to_sorted_list(value), durable=durable)

    def quote_used_history_has_legacy_indices(self, value: set) -> bool:
        """Return whether quote used history has legacy indices."""
        return any(re.fullmatch(r"-?\d+", str(item)) for item in value)

    def quote_source_matches_analysis(self, quote_analysis: dict | None, lines: list[str]) -> bool:
        """Return whether quote source matches analysis."""
        if not isinstance(quote_analysis, dict):
            return False
        source = quote_analysis.get("source", {}) if isinstance(quote_analysis.get("source"), dict) else {}
        expected = source.get("source_sha256")
        if not expected:
            return False
        current = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
        return str(expected) == current

    def normalise_quote_used_hashes(
        self,
        raw_used: set,
        lines: list[str],
        quote_analysis: dict | None = None,
    ) -> tuple[set, bool]:
        """Return whether normalise quote used hashes."""
        hashes_by_line = self.quote_candidates.hashes_by_line(lines)
        normalised: set[str] = set()
        changed = False
        can_migrate_indices = self.quote_source_matches_analysis(quote_analysis, lines)

        for item in raw_used:
            item_text = str(item)
            if re.fullmatch(r"[0-9a-fA-F]{64}", item_text):
                normalised.add(item_text.lower())
                if item_text != item_text.lower():
                    changed = True
                continue
            try:
                line_no = int(item)
            except Exception:
                self.log.warning("Dropping unrecognised quote used-history entry: %r", item)
                changed = True
                continue
            if not can_migrate_indices:
                normalised.add(item)
                continue
            if line_no in hashes_by_line:
                normalised.add(hashes_by_line[line_no])
            else:
                self.log.warning("Dropping out-of-range quote line used-history entry: %r", item)
            changed = True

        return normalised, changed or normalised != {str(item) for item in raw_used}

    def load_quote_used_hashes(self, lines: list[str]) -> set[str]:
        """Return whether load quote used hashes."""
        raw = self.load_used_set(self.quote_history_file, legacy_pickle_path=self.legacy_quote_file)
        quote_analysis = self.metadata.load_quote()
        normalised, changed = self.normalise_quote_used_hashes(raw, lines, quote_analysis)
        if self.quote_used_history_has_legacy_indices(normalised):
            self.log.critical(
                "Quote used-history contains legacy integer entries but current quote source does not match analysed source; refusing destructive migration"
            )
            return normalised
        if changed or self.quote_history_file.exists():
            self.save_used_set(self.quote_history_file, normalised)
            self.log.info("Quote used-history normalised to %d quote hash(es)", len(normalised))
        return normalised

    def save_quote_used_hashes(self, path: Path, value: set[str], *, durable: bool = False) -> None:
        """Return whether save quote used hashes."""
        self.save_used_set(path, {str(item) for item in value}, durable=durable)

    def save_image_used_basenames(self, path: Path, value: set[str], *, durable: bool = False) -> None:
        """Save image used basenames."""
        self.write_json(
            path,
            sorted(str(item) for item in value),
            durable=durable,
        )

    def image_used_history_has_legacy_indices(self, images_used: set) -> bool:
        """Use the shared legacy-index test for image history."""
        return self.quote_used_history_has_legacy_indices(images_used)

    def image_corpus_verified_for_legacy_migration(self, images: list[str], image_analysis: dict | None) -> bool:
        """Prove metadata completeness and the unchanged legacy index order."""
        if not isinstance(image_analysis, dict):
            return False
        expected = set(str(name) for name in (image_analysis.get("path_index") or {}).keys())
        visible = {Path(path).name for path in images}
        return bool(expected) and visible == expected and images == self.metadata.original_image_paths()

    def load_image_used_basenames(self, images: list[str]) -> set:
        """Load image used basenames."""
        raw = self.load_used_set(self.image_history_file, legacy_pickle_path=self.legacy_image_file)
        image_analysis = self.metadata.load_image()
        normalised, changed = self.normalise_image_used_basenames(raw, images, image_analysis)
        if self.image_used_history_has_legacy_indices(normalised) and images:
            self.log.critical(
                "Image used-history contains legacy integer entries but current image corpus is not verified complete; refusing destructive migration"
            )
            return normalised
        if images and (changed or self.image_history_file.exists()):
            self.save_image_used_basenames(self.image_history_file, normalised)
            self.log.info("Image used-history normalised to %d basename(s)", len(normalised))
        elif not images and changed:
            self.log.warning("Image scan is empty; preserving image used-history without rewriting %s", self.image_history_file)
        return normalised

    def normalise_image_used_basenames(
        self, images_used: set, images: list[str], image_analysis: dict | None = None,
    ) -> tuple[set, bool]:
        """Normalize image history with this owner's current corpus proof."""
        return normalise_image_used_basenames(
            images_used, images, image_analysis,
            image_corpus_verified_for_legacy_migration=self.image_corpus_verified_for_legacy_migration,
        )


def normalise_image_used_basenames(
    images_used: set,
    images: list[str],
    image_analysis: dict | None = None,
    *,
    image_corpus_verified_for_legacy_migration: Any,
) -> tuple[set, bool]:
    """Normalise image used basenames."""
    basenames = [Path(path).name for path in images]
    migrated: set = set()
    changed = False
    can_migrate_indices = image_corpus_verified_for_legacy_migration(images, image_analysis)

    for item in images_used:
        item_text = str(item)
        if not re.fullmatch(r"-?\d+", item_text):
            migrated.add(item_text)
            continue
        if not can_migrate_indices:
            migrated.add(item)
            continue
        try:
            index = int(item)
        except Exception:
            migrated.add(item)
            continue
        if 0 <= index < len(basenames):
            migrated.add(basenames[index])
            changed = True
        else:
            migrated.add(item)

    if {str(item) for item in migrated} != {str(item) for item in images_used}:
        changed = True
    return migrated, changed
