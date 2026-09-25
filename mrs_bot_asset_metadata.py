"""Asset metadata loading, merging, identity checks and catalog discovery.

Explicit calls read the supplied quote/image/meme metadata and discover regular
original-glob images plus PNGs in the same configured directory. The coordinator
supplies current configuration, helpers, logger and stale image exception; it
retains eligibility, selection, cache and persistence
responsibilities. AssetMetadata binds current external inputs per operation and
may be shared by the candidate, history, editorial and image-selection owners
for one root call. It uses its owned loaders/overrides directly. Pure
normalization, hashing and merge recursion stay local. Import and construction
do no runtime work; loaders retain their existing fallback/error boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass

import hashlib
import json
import re
from collections.abc import Callable
from logging import Logger
from pathlib import Path


def collapse_quote_whitespace(text: str) -> str:
    """Collapse quote whitespace."""
    return re.sub(r"\s+", " ", str(text or "").strip())


def quote_text_hash(text: str) -> str:
    """Return whether quote text hash."""
    return hashlib.sha256(collapse_quote_whitespace(text).encode("utf-8")).hexdigest()


def deep_merge_dict(base: dict, patch: dict) -> dict:
    """Return the deep merge dict."""
    merged = json.loads(json.dumps(base))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = json.loads(json.dumps(value))
    return merged


def generated_image_origin_quote_hash(basename: str) -> str | None:
    """Return whether generated image origin quote hash."""
    match = re.fullmatch(r"tg_([0-9a-fA-F]{64})\.[A-Za-z0-9]+", str(basename))
    if not match:
        return None
    return match.group(1).lower()


@dataclass(frozen=True)
class AssetMetadata:
    """Own shared asset loading, overrides, identity checks and catalog discovery."""

    log: Logger
    quote_file: Path
    quote_overrides_file: Path
    image_file: Path
    image_glob: str
    glob: Callable[[str], list[str]]
    image_sha256: Callable[[str], str]
    stale_image_metadata: type[Exception]
    meme_file: Path

    def load_json(
        self,
        path: Path,
        *,
        label: str,
    ) -> dict | None:
        """Load JSON object."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            self.log.warning("%s file missing: %s", label, path)
            return None
        except Exception:
            self.log.exception("Failed loading %s file: %s", label, path)
            return None
        if not isinstance(data, dict):
            self.log.warning("%s file is not a JSON object: %s", label, path)
            return None
        return data

    def apply_quote_overrides(
        self,
        raw_analysis: dict,
        overrides: dict | None,
    ) -> dict:
        """Apply quote analysis overrides."""
        if not overrides:
            return raw_analysis

        merged = json.loads(json.dumps(raw_analysis))
        quote_overrides = overrides.get("quote_overrides", {})
        if not isinstance(quote_overrides, dict):
            self.log.warning("Quote analysis override file has invalid quote_overrides")
            return merged

        items = merged.get("items", {})
        for quote_hash, override in quote_overrides.items():
            if not isinstance(override, dict):
                self.log.warning("Skipping quote override %s: override is not an object", quote_hash)
                continue

            item = items.get(str(quote_hash))
            if not isinstance(item, dict):
                self.log.warning("Skipping quote override %s: quote hash does not exist", quote_hash)
                continue

            expected_text = override.get("expected_text")
            if expected_text is not None and expected_text != item.get("text"):
                self.log.warning("Skipping quote override %s: expected_text does not match current quote text", quote_hash)
                continue

            line_numbers = {int(value) for value in item.get("line_numbers", []) if str(value).isdigit()}
            expected_lines = override.get("expected_line_numbers", [])
            try:
                expected_line_numbers = {int(value) for value in expected_lines}
            except Exception:
                self.log.warning("Skipping quote override %s: expected_line_numbers is invalid", quote_hash)
                continue
            if not expected_line_numbers.issubset(line_numbers):
                self.log.warning(
                    "Skipping quote override %s: expected lines %s not present in record lines %s",
                    quote_hash,
                    sorted(expected_line_numbers),
                    sorted(line_numbers),
                )
                continue

            patch = override.get("analysis_patch")
            if not isinstance(patch, dict):
                self.log.warning("Skipping quote override %s: analysis_patch is not an object", quote_hash)
                continue

            analysis = item.get("analysis")
            if not isinstance(analysis, dict):
                self.log.warning("Skipping quote override %s: raw analysis is not an object", quote_hash)
                continue
            item["analysis"] = deep_merge_dict(analysis, patch)
            self.log.info("Applied quote analysis override for hash=%s reason=%s", quote_hash, override.get("reason"))

        return merged

    def load_quote(
        self,
    ) -> dict | None:
        """Load validated quotation-analysis metadata and local overrides."""
        raw = self.load_json(self.quote_file, label="quote analysis")
        if raw is None:
            return None
        if raw.get("analysis_kind") != "quotes":
            self.log.error("Quote analysis file has unsupported analysis_kind=%r", raw.get("analysis_kind"))
            return None
        if raw.get("schema_version") != 2:
            self.log.error("Quote analysis file has unsupported schema_version=%r", raw.get("schema_version"))
            return None
        if not isinstance(raw.get("items"), dict):
            self.log.error("Quote analysis file has invalid or missing items object: %s", self.quote_file)
            return None
        overrides = self.load_json(self.quote_overrides_file, label="quote analysis override")
        return self.apply_quote_overrides(raw, overrides)

    def load_image_file(
        self,
        path: Path,
        *,
        label: str,
    ) -> dict | None:
        """Load image analysis file."""
        raw = self.load_json(path, label=label)
        if raw is None:
            return None
        if raw.get("analysis_kind") != "images":
            self.log.error("%s file has unsupported analysis_kind=%r", label, raw.get("analysis_kind"))
            return None
        if raw.get("schema_version") != 3:
            self.log.error("%s file has unsupported schema_version=%r", label, raw.get("schema_version"))
            return None
        if not isinstance(raw.get("items"), dict) or not isinstance(raw.get("path_index"), dict):
            self.log.error("%s file has invalid required structure: %s", label, path)
            return None
        return raw

    def load_image(
        self,
    ) -> dict | None:
        """Load metadata for the regular quotation-image corpus."""
        return self.load_image_file(self.image_file, label="image analysis")

    def quote_for_hash(
        self,
        quote_analysis: dict | None,
        quote_hash: str,
        text: str = '',
    ) -> dict | None:
        """Return whether quote metadata for hash."""
        if not isinstance(quote_analysis, dict):
            return None
        item = (quote_analysis.get("items") or {}).get(str(quote_hash), {})
        if not isinstance(item, dict):
            self.log.warning("Quote metadata missing for current quote hash=%s text=%r", quote_hash, collapse_quote_whitespace(text)[:120])
            return None
        analysed_text = item.get("text")
        if analysed_text is not None and quote_text_hash(str(analysed_text)) != quote_hash:
            self.log.warning("Quote metadata stale for hash=%s: analysed text does not match hash", quote_hash)
            return None
        analysis = item.get("analysis")
        if not isinstance(analysis, dict):
            self.log.warning("Quote metadata missing analysis object for hash=%s", quote_hash)
            return None
        return analysis

    def validate_quote_lines(
        self,
        quote_analysis: dict,
        lines: list[str],
    ) -> None:
        """Validate quote analysis against lines."""
        source = quote_analysis.get("source", {}) if isinstance(quote_analysis.get("source"), dict) else {}
        expected_source_sha = source.get("source_sha256")
        if expected_source_sha:
            current_source_sha = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
            if str(expected_source_sha) != current_source_sha:
                self.log.warning(
                    "Quote source SHA differs from analysed source: current=%s analysed=%s; per-quote hashes will be used",
                    current_source_sha,
                    expected_source_sha,
                )

    def original_image_paths(self) -> list[str]:
        """Return the sorted legacy glob pool used to prove numeric history."""
        images = self.glob(self.image_glob)
        images.sort()
        return [
            path for path in images
            if Path(path).is_file()
            and Path(path).suffix.lower() in {".jpg", ".jpeg", ".png"}
            and generated_image_origin_quote_hash(Path(path).name) is None
        ]

    def image_paths(
        self,
    ) -> list[str]:
        """Discover original-glob images and PNGs in the same regular directory."""
        images = set(self.original_image_paths())
        image_dir = Path(self.image_glob).parent
        if image_dir.is_dir():
            images.update(
                str(path) for path in image_dir.iterdir()
                if path.is_file()
                and path.suffix.lower() == ".png"
                and generated_image_origin_quote_hash(path.name) is None
            )
        return sorted(images)

    def image_for_basename(
        self,
        image_analysis: dict | None,
        basename: str,
        path: str | None = None,
    ) -> tuple[str | None, dict | None]:
        """Return the image metadata for basename."""
        if not isinstance(image_analysis, dict):
            return None, None
        image_hash = (image_analysis.get("path_index") or {}).get(basename)
        if not image_hash:
            self.log.warning("Image %s is absent from image analysis; excluding until analysed", basename)
            raise self.stale_image_metadata(f"Image metadata missing for {basename}")
        image_hash = str(image_hash)
        if path is not None:
            try:
                current_hash = self.image_sha256(path)
            except Exception:
                self.log.exception("Could not hash current image for metadata validation: %s", path)
                raise self.stale_image_metadata(f"Image content could not be verified for {basename}")
            if current_hash != image_hash:
                self.log.warning(
                    "Image metadata stale for basename=%s: current_hash=%s analysed_hash=%s; excluding until reanalysed",
                    basename,
                    current_hash,
                    image_hash,
                )
                raise self.stale_image_metadata(f"Image metadata stale for {basename}")
        item = (image_analysis.get("items") or {}).get(str(image_hash), {})
        analysis = item.get("analysis") if isinstance(item, dict) else None
        if not isinstance(analysis, dict):
            self.log.warning("Image %s has no valid per-image analysis for hash=%s; excluding until reanalysed", basename, image_hash)
            raise self.stale_image_metadata(f"Image analysis missing or invalid for {basename}")
        return str(image_hash), analysis

    def load_meme_index(
        self,
    ) -> dict[str, dict]:
        """Load meme analysis index."""
        self.log.debug("Loading meme analysis from %s", self.meme_file)

        try:
            with open(self.meme_file, "r") as f:
                data = json.load(f)
        except Exception:
            self.log.exception("Failed loading meme analysis file: %s", self.meme_file)
            return {}

        index: dict[str, dict] = {}

        for item in data.get("results", []):
            filename = item.get("filename")
            path = item.get("path")
            output_filename = item.get("output_filename")

            if filename:
                index[str(filename)] = item

            if path:
                index[Path(str(path)).name] = item

            if output_filename:
                index[str(output_filename)] = item

        self.log.info("Loaded meme analysis entries=%d", len(index))
        return index
