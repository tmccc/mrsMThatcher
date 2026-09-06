"""Durable quote and image used history.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


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


def load_used_set(
    path: Path,
    *,
    legacy_pickle_path: Path | None = None,
    CorruptUsedHistoryError: Any,
    UnsafeDurableStateNamespace: Any,
    coerce_used_set: Any,
    json: Any,
    log: Any,
    read_stable_owned_json_bytes_no_follow: Any,
    save_used_set: Any,
    used_set_to_sorted_list: Any,
) -> set:
    """Load a fail-closed durable used-history set."""
    log.debug("Loading used-history set from %s", path)

    try:
        present, data = read_stable_owned_json_bytes_no_follow(path)
        if not present or data is None:
            raise FileNotFoundError(path)
        value = json.loads(data.decode("utf-8"))
        converted = coerce_used_set(value, path=path)
        if isinstance(value, list) and value != used_set_to_sorted_list(converted):
            save_used_set(path, converted)
            log.info("Normalized used-history JSON ordering in %s", path)
        log.debug("Loaded %d entries from %s", len(converted), path)
        return converted
    except FileNotFoundError:
        log.warning("Used-history JSON file does not exist yet: %s", path)
    except (OSError, UnsafeDurableStateNamespace):
        log.exception("OS error loading existing used-history JSON file %s; refusing stale legacy fallback", path)
        raise CorruptUsedHistoryError(f"Existing used-history JSON is unreadable: {path}")
    except Exception:
        log.exception("Failed loading existing used-history JSON file %s; refusing stale legacy fallback", path)
        raise CorruptUsedHistoryError(f"Existing used-history JSON is corrupt or invalid: {path}")

    if legacy_pickle_path is not None and legacy_pickle_path.exists():
        log.critical(
            "Used-history JSON %s is missing but legacy pickle %s exists; refusing unsafe pickle fallback. "
            "Restore the JSON history or migrate manually from a trusted backup.",
            path,
            legacy_pickle_path,
        )
        raise CorruptUsedHistoryError(f"Used-history JSON missing while legacy pickle exists: {path}")
    return set()


def save_used_set(
    path: Path,
    value: set,
    *,
    durable: bool = False,
    atomic_write_json: Any,
    log: Any,
    used_set_to_sorted_list: Any,
) -> None:
    """Persist a used-history set atomically."""
    log.debug("Saving %d entries to used-history JSON %s", len(value), path)
    atomic_write_json(path, used_set_to_sorted_list(value), durable=durable)


def quote_used_history_has_legacy_indices(
    value: set,
    *,
    re: Any,
) -> bool:
    """Return whether quote used history has legacy indices."""
    return any(re.fullmatch(r"-?\d+", str(item)) for item in value)


def quote_source_matches_analysis(
    quote_analysis: dict | None,
    lines: list[str],
    *,
    hashlib: Any,
) -> bool:
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
    raw_used: set,
    lines: list[str],
    quote_analysis: dict | None = None,
    *,
    current_quote_hashes_by_line: Any,
    log: Any,
    quote_source_matches_analysis: Any,
    re: Any,
) -> tuple[set, bool]:
    """Return whether normalise quote used hashes."""
    hashes_by_line = current_quote_hashes_by_line(lines)
    normalised: set[str] = set()
    changed = False
    can_migrate_indices = quote_source_matches_analysis(quote_analysis, lines)

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
            log.warning("Dropping unrecognised quote used-history entry: %r", item)
            changed = True
            continue
        if not can_migrate_indices:
            normalised.add(item)
            continue
        if line_no in hashes_by_line:
            normalised.add(hashes_by_line[line_no])
        else:
            log.warning("Dropping out-of-range quote line used-history entry: %r", item)
        changed = True

    return normalised, changed or normalised != {str(item) for item in raw_used}


def load_quote_used_hashes(
    lines: list[str],
    *,
    LINES_USED_FILE: Any,
    PICKLE_FILE: Any,
    load_quote_analysis: Any,
    load_used_set: Any,
    log: Any,
    normalise_quote_used_hashes: Any,
    quote_used_history_has_legacy_indices: Any,
    save_used_set: Any,
) -> set[str]:
    """Return whether load quote used hashes."""
    raw = load_used_set(LINES_USED_FILE, legacy_pickle_path=PICKLE_FILE)
    quote_analysis = load_quote_analysis()
    normalised, changed = normalise_quote_used_hashes(raw, lines, quote_analysis)
    if quote_used_history_has_legacy_indices(normalised):
        log.critical(
            "Quote used-history contains legacy integer entries but current quote source does not match analysed source; refusing destructive migration"
        )
        return normalised
    if changed or LINES_USED_FILE.exists():
        save_used_set(LINES_USED_FILE, normalised)
        log.info("Quote used-history normalised to %d quote hash(es)", len(normalised))
    return normalised


def save_quote_used_hashes(
    path: Path,
    value: set[str],
    *,
    durable: bool = False,
    save_used_set: Any,
) -> None:
    """Return whether save quote used hashes."""
    save_used_set(path, {str(item) for item in value}, durable=durable)


def save_image_used_basenames(
    path: Path,
    value: set[str],
    *,
    durable: bool = False,
    atomic_write_json: Any,
) -> None:
    """Save image used basenames."""
    atomic_write_json(
        path,
        sorted(str(item) for item in value),
        durable=durable,
    )


def image_used_history_has_legacy_indices(
    images_used: set,
    *,
    re: Any,
) -> bool:
    """Return whether image used history has legacy indices."""
    return any(re.fullmatch(r"-?\d+", str(item)) for item in images_used)


def image_corpus_verified_for_legacy_migration(
    images: list[str],
    image_analysis: dict | None,
    *,
    ENABLE_GENERATED_IMAGE_POOL: Any,
    Path: Any,
) -> bool:
    """Return the image corpus verified for legacy migration."""
    if ENABLE_GENERATED_IMAGE_POOL:
        return False
    if not isinstance(image_analysis, dict):
        return False
    expected = set(str(name) for name in (image_analysis.get("path_index") or {}).keys())
    visible = {Path(path).name for path in images}
    return bool(expected) and visible == expected


def normalise_image_used_basenames(
    images_used: set,
    images: list[str],
    image_analysis: dict | None = None,
    *,
    Path: Any,
    image_corpus_verified_for_legacy_migration: Any,
    re: Any,
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


def load_image_used_basenames(
    images: list[str],
    *,
    IMAGES_USED_FILE: Any,
    IMAGE_PICKLE_FILE: Any,
    image_used_history_has_legacy_indices: Any,
    load_image_analysis: Any,
    load_used_set: Any,
    log: Any,
    normalise_image_used_basenames: Any,
    save_image_used_basenames: Any,
) -> set:
    """Load image used basenames."""
    raw = load_used_set(IMAGES_USED_FILE, legacy_pickle_path=IMAGE_PICKLE_FILE)
    image_analysis = load_image_analysis()
    normalised, changed = normalise_image_used_basenames(raw, images, image_analysis)
    if image_used_history_has_legacy_indices(normalised) and images:
        log.critical(
            "Image used-history contains legacy integer entries but current image corpus is not verified complete; refusing destructive migration"
        )
        return normalised
    if images and (changed or IMAGES_USED_FILE.exists()):
        save_image_used_basenames(IMAGES_USED_FILE, normalised)
        log.info("Image used-history normalised to %d basename(s)", len(normalised))
    elif not images and changed:
        log.warning("Image scan is empty; preserving image used-history without rewriting %s", IMAGES_USED_FILE)
    return normalised
