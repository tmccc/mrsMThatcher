"""Pre-score images and build deterministic quotation shortlists."""

from __future__ import annotations

import re
from typing import Any, Iterable

TOKEN = re.compile(r"[a-z0-9]+")


def terms(values: Iterable[str]) -> set[str]:
    """Return the terms."""
    return {token for value in values for token in TOKEN.findall(str(value).lower()) if len(token) > 2}


def pre_score(quote: dict[str, Any], image: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Return the pre score."""
    q_primary = terms([quote.get("primary_issue", ""), *quote.get("primary_themes", [])])
    i_primary = terms([image.get("primary_issue", ""), *image.get("primary_themes", [])])
    q_secondary = terms([*quote.get("secondary_themes", []), *quote.get("specific_concepts", [])])
    i_secondary = terms([*image.get("secondary_messages", []), *image.get("specific_concepts", []), *image.get("visual_evidence", [])])
    desired = terms(quote.get("desired_visual_evidence", []))
    not_about = terms(quote.get("not_about", []))
    all_image = i_primary | i_secondary
    primary_match = quote.get("primary_issue") == image.get("primary_issue")
    primary_overlap = len(q_primary & i_primary) / max(1, len(q_primary))
    secondary_overlap = len(q_secondary & all_image) / max(1, len(q_secondary))
    visual_overlap = len(desired & all_image) / max(1, len(desired))
    conflict = len(not_about & all_image) / max(1, len(not_about))
    confidence = min(float(quote.get("confidence", 0)), float(image.get("confidence", 0)))
    score = 0.35 * float(primary_match) + 0.2 * primary_overlap + 0.2 * secondary_overlap + 0.15 * visual_overlap + 0.1 * confidence - 0.35 * conflict
    signals = {
        "primary_issue_match": primary_match,
        "primary_theme_overlap": round(primary_overlap, 6),
        "secondary_concept_overlap": round(secondary_overlap, 6),
        "desired_visual_overlap": round(visual_overlap, 6),
        "not_about_conflict": round(conflict, 6),
        "confidence": round(confidence, 6),
    }
    return round(score, 6), signals


def build_shortlist(quote: dict[str, Any], images: Iterable[dict[str, Any]], *, limit: int = 15, forced: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Build shortlist."""
    forced = forced or {}
    rows = []
    for image in images:
        score, signals = pre_score(quote, image)
        basename = image["image_basename"]
        rows.append({"quote_hash": quote["quote_hash"], "image_basename": basename, "pre_score": score, "forced_reason": forced.get(basename), "signals": signals})
    rows.sort(key=lambda row: (-row["pre_score"], row["image_basename"]))
    selected = rows[: max(0, limit)]
    selected_names = {row["image_basename"] for row in selected}
    selected.extend(row for row in rows if row["forced_reason"] and row["image_basename"] not in selected_names)
    return sorted(selected, key=lambda row: (-row["pre_score"], row["image_basename"]))
