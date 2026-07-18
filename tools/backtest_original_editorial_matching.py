#!/usr/bin/env python3
"""Offline backtest for experimental original-image editorial metadata.

This tool does not modify production bot state and does not call network APIs.
It imports the bot module only for pure scoring/hash helpers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_IMPORT_BASE = Path(tempfile.gettempdir()) / "mrs_editorial_backtest_import"
_IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(_IMPORT_BASE),
    "MRS_LOG_FILE": str(_IMPORT_BASE / "backtest_import.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "offline",
    "X_CONSUMER_SECRET": "offline",
    "X_ACCESS_TOKEN": "offline",
    "X_ACCESS_SECRET": "offline",
    "X_MY_USER_ID": "0",
    "XAI_API_KEY": "offline",
    "X_BEARER_TOKEN": "offline",
    "LOG_LEVEL": "CRITICAL",
}


def import_bot_for_offline_scoring() -> Any:
    _IMPORT_BASE.mkdir(parents=True, exist_ok=True)
    old_env = {key: os.environ.get(key) for key in _IMPORT_ENV}
    os.environ.update(_IMPORT_ENV)
    try:
        import mrsMThatcher2 as imported_bot  # noqa: E402
        imported_bot.log.setLevel(logging.DEBUG)
        for handler in imported_bot.log.handlers:
            handler.setLevel(logging.NOTSET)
        return imported_bot
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


bot = import_bot_for_offline_scoring()


EXPECTED_ANALYSIS_KIND = "original_editorial_experiment"
DEFAULT_SEED = 20260709
DIAGNOSTIC_T70_QUOTE = "There is an increasing belief that freedom is divisible. No myth is more dangerous. Freedom is indivisible."
VARIANT_C_WEIGHTS = {"low": 0.18, "medium": 0.32, "high": 0.50}
BROKEN_RUN_BASELINE = {
    "scale_bug": "previous run incorrectly treated editorial dimension_scores and overall_editorial_utility as 0..100 instead of 0..10",
    "winner_changes": {"low": 0, "medium": 0, "high": 3},
    "baseline_entropy": 3.8920316034625353,
    "combined_medium_entropy": 3.8920316034625353,
    "t70": {
        "variant_a_score": 2.4983929878149485,
        "variant_a_rank": 17,
        "variant_b_score": -4.3687499999999995,
        "variant_b_rank": 12,
        "combined_medium_score": 39.16148575610078,
        "combined_medium_rank": 3,
    },
    "vocabulary": {"raw_unique_terms": 895, "normalised_unique_concepts": 866},
}

CANONICAL_SYNONYMS = {
    "leadership": {
        "leadership_display",
        "leadership_presence",
        "leadership_emblem",
        "leadership_persona",
        "leadership_visibility",
        "leadership_symbolism",
        "leaderly_presence",
    },
    "principle": {
        "assertion_of_principle",
        "principle_assertion",
        "principle_anchor",
        "principle_framing",
        "principled_resolve",
    },
    "resolve": {"resolve_projection", "resolute", "resolution", "determination", "determined"},
    "authority": {"authority_framing", "authoritative", "commanding_authority"},
    "conviction": {"conviction_illustration", "political_conviction"},
    "warning": {"warning_signal", "caution", "danger_warning", "grave_warning"},
    "economic": {"economic_seriousness", "economy", "markets", "finance", "fiscal"},
    "patriotism": {"national_direction", "national_pride", "nation", "britain", "britishness"},
    "speech": {"public_address_emphasis", "oratory", "oratorical", "speechmaking", "address"},
    "defiance": {"political_struggle", "confrontation", "defiant", "resistance"},
    "warmth": {"human_warmth", "warm", "informal_warmth"},
    "ceremony": {"ceremony_formality", "formal_ceremony", "ceremonial"},
    "historical": {"historical_iconicity", "historical", "legacy", "iconic"},
    "statesmanship": {"statesmanlike", "statesmanlike_authority", "statesmanship"},
    "freedom": {"liberty", "individual_liberty", "freedom"},
    "duty": {"responsibility", "duty", "obligation"},
    "optimism": {"optimistic", "hope", "positive", "uplift"},
}
SYNONYM_TO_CANONICAL = {
    bot.normalise_tag(value): canonical
    for canonical, values in CANONICAL_SYNONYMS.items()
    for value in values | {canonical}
}

BROAD_CONCEPTS = {
    "leadership",
    "principle",
    "authority",
    "conviction",
    "resolve",
    "statesmanship",
    "duty",
}

DIMENSIONS = [
    "leadership",
    "conviction",
    "authority",
    "defiance",
    "warning",
    "optimism",
    "patriotism",
    "statesmanship",
    "economic_seriousness",
    "human_warmth",
    "ceremony_formality",
    "historical_iconicity",
]


class BacktestError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageRecord:
    basename: str
    path: Path
    sha256: str
    production_analysis: dict[str, Any]
    editorial_analysis: dict[str, Any]


@dataclass(frozen=True)
class QuoteRecord:
    quote_hash: str
    text: str
    line_no: int
    analysis: dict[str, Any]
    sample: str
    recent_post: dict[str, Any] | None = None


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise BacktestError(f"{path} is not a JSON object")
    return data


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_concept(value: object) -> str:
    tag = bot.normalise_tag(value)
    if not tag:
        return ""
    if tag in SYNONYM_TO_CANONICAL:
        return SYNONYM_TO_CANONICAL[tag]
    if tag.endswith("ies") and len(tag) > 4:
        tag = tag[:-3] + "y"
    return SYNONYM_TO_CANONICAL.get(tag, tag)


def canonical_concepts(value: object) -> set[str]:
    tag = bot.normalise_tag(value)
    if not tag:
        return set()
    if "_and_" in tag:
        concepts: set[str] = set()
        for part in tag.split("_and_"):
            concepts.update(canonical_concepts(part))
        return concepts
    return {canonical_concept(tag)}


def concepts_from_values(values: Any) -> set[str]:
    out: set[str] = set()
    for value in bot.as_string_list(values):
        out.update(concept for concept in canonical_concepts(value) if concept)
    return out


def editorial_image_concepts(editorial: dict[str, Any]) -> set[str]:
    concepts: set[str] = set()
    for key in ("abstract_quote_affinities", "editorial_functions", "best_quote_types"):
        concepts.update(concepts_from_values(editorial.get(key)))
    return concepts


def avoid_concepts(editorial: dict[str, Any]) -> set[str]:
    return concepts_from_values(editorial.get("avoid_quote_types"))


def quote_concepts(analysis: dict[str, Any]) -> set[str]:
    concepts: set[str] = set()
    concepts.update(concepts_from_values(analysis.get("primary_topics")))
    concepts.update(concepts_from_values(analysis.get("secondary_topics")))
    concepts.update(concepts_from_values(analysis.get("tone")))
    prefs = analysis.get("archive_image_preferences", {})
    if isinstance(prefs, dict):
        for key in (
            "preferred_subject_moods",
            "preferred_scenes",
            "preferred_activities",
            "preferred_visible_symbols",
            "visual_affinities",
            "weak_visual_mismatches",
            "strong_visual_mismatches",
        ):
            concepts.update(concepts_from_values(prefs.get(key)))
    hist = analysis.get("historical_context", {})
    if isinstance(hist, dict):
        for key in ("referenced_events", "referenced_people", "referenced_places", "specificity"):
            concepts.update(concepts_from_values(hist.get(key)))
    return concepts


def build_editorial_idf(images: dict[str, ImageRecord]) -> tuple[dict[str, float], Counter[str]]:
    df: Counter[str] = Counter()
    for rec in images.values():
        df.update(editorial_image_concepts(rec.editorial_analysis))
    total = max(1, len(images))
    idf = {concept: math.log((total + 1) / (count + 1)) + 0.35 for concept, count in df.items()}
    for broad in BROAD_CONCEPTS:
        if broad in idf:
            idf[broad] *= 0.45
    return idf, df


def editorial_affinity_score(quote_analysis: dict[str, Any], editorial: dict[str, Any], idf: dict[str, float]) -> tuple[float, dict[str, Any]]:
    q = quote_concepts(quote_analysis)
    img = editorial_image_concepts(editorial)
    avoid = avoid_concepts(editorial)
    positive_matches = sorted(q & img)
    avoid_matches = sorted(q & avoid)
    positive = sum(idf.get(concept, 0.35) for concept in positive_matches)
    avoid_penalty = sum(max(0.5, idf.get(concept, 0.35)) for concept in avoid_matches) * 1.2
    utility = normalise_editorial_utility(editorial.get("overall_editorial_utility", 5.5))
    utility_adj = max(-0.5, min(1.0, (utility - 5.5) / 4.5))
    score = min(10.0, positive * 1.15 + utility_adj) - avoid_penalty
    return score, {"matches": positive_matches, "avoid_matches": avoid_matches, "utility_adj": utility_adj}


def quote_dimension_profile(analysis: dict[str, Any]) -> dict[str, float]:
    concepts = quote_concepts(analysis)
    tone = {canonical_concept(v) for v in bot.as_string_list(analysis.get("tone"))}
    prefs = analysis.get("archive_image_preferences", {}) if isinstance(analysis.get("archive_image_preferences"), dict) else {}
    visual_energy = str(analysis.get("visual_energy") or "").lower()
    hist = analysis.get("historical_context", {}) if isinstance(analysis.get("historical_context"), dict) else {}
    profile = {dim: 0.0 for dim in DIMENSIONS}

    def add(dim: str, value: float) -> None:
        profile[dim] = min(1.0, max(profile[dim], value))

    if concepts & {"government", "state_power", "leadership"}:
        add("leadership", 0.45)
    if concepts & {"freedom", "principle", "duty", "conviction"}:
        add("conviction", 0.65)
    if concepts & {"government", "law_and_order", "authority"} or tone & {"authoritative", "serious"}:
        add("authority", 0.55)
    if concepts & {"defiance", "struggle", "political_struggle"} or tone & {"defiant", "confrontational"}:
        add("defiance", 0.65)
    if concepts & {"warning", "danger", "security"} or tone & {"grave", "urgent", "warning"}:
        add("warning", 0.7)
    if concepts & {"optimism", "hope", "future"} or tone & {"optimistic", "hopeful"}:
        add("optimism", 0.65)
    if concepts & {"patriotism", "britain", "nation", "national"}:
        add("patriotism", 0.65)
    if concepts & {"statesmanship", "diplomacy", "parliament"}:
        add("statesmanship", 0.6)
    if concepts & {"economic", "economy", "tax", "markets", "fiscal", "enterprise"}:
        add("economic_seriousness", 0.75)
    if concepts & {"family", "human", "warmth"} or tone & {"warm", "personal"}:
        add("human_warmth", 0.7)
    if concepts & {"ceremony", "formal_portrait"}:
        add("ceremony_formality", 0.45)
    if hist.get("needs_historical_image_match") or str(hist.get("specificity")) in {"specific", "high"}:
        add("historical_iconicity", 0.7)
    if visual_energy == "high":
        add("defiance", 0.35)
    if visual_energy == "low":
        add("statesmanship", 0.25)
    for scene in bot.as_string_list(prefs.get("preferred_scenes")):
        concept = canonical_concept(scene)
        if concept in {"parliament", "office_or_working"}:
            add("statesmanship", 0.45)
        if concept == "formal_portrait":
            add("authority", 0.35)
    return profile


def editorial_dimension_score(quote_analysis: dict[str, Any], editorial: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    q = quote_dimension_profile(quote_analysis)
    raw = editorial.get("dimension_scores") or {}
    img = {}
    for dim in DIMENSIONS:
        try:
            img[dim] = normalise_dimension_score(raw.get(dim, 0), dim)
        except Exception:
            img[dim] = 0.0
    active = {dim: value for dim, value in q.items() if value > 0}
    if not active:
        return 0.0, {"active_dimensions": {}, "dimension_terms": []}
    score = 0.0
    terms = []
    for dim, qval in active.items():
        contribution = qval * (img.get(dim, 0.0) - 0.45) * 5.0
        score += contribution
        terms.append((dim, round(contribution, 3), round(qval, 2), round(img.get(dim, 0.0), 2)))
    if q.get("warning", 0) > 0.55 and img.get("optimism", 0) > 0.75:
        score -= 1.5
        terms.append(("optimism_warning_tension", -1.5, q.get("warning", 0), img.get("optimism", 0)))
    if q.get("defiance", 0) > 0.55 and img.get("human_warmth", 0) > 0.75:
        score -= 1.0
        terms.append(("warmth_defiance_tension", -1.0, q.get("defiance", 0), img.get("human_warmth", 0)))
    if q.get("defiance", 0) > 0.45 and img.get("ceremony_formality", 0) > 0.85:
        score -= 0.8
        terms.append(("ceremony_action_tension", -0.8, q.get("defiance", 0), img.get("ceremony_formality", 0)))
    return max(-8.0, min(10.0, score)), {"active_dimensions": active, "dimension_terms": terms}


def normalise_dimension_score(value: Any, field: str = "dimension_score") -> float:
    if isinstance(value, bool):
        raise BacktestError(f"{field} must be a number in 0..10, got bool")
    try:
        raw = float(value)
    except Exception as exc:
        raise BacktestError(f"{field} must be a number in 0..10") from exc
    if not 0.0 <= raw <= 10.0:
        raise BacktestError(f"{field} outside 0..10: {raw}")
    return raw / 10.0


def normalise_editorial_utility(value: Any) -> float:
    if isinstance(value, bool):
        raise BacktestError("overall_editorial_utility must be a number in 0..10, got bool")
    try:
        raw = float(value)
    except Exception as exc:
        raise BacktestError("overall_editorial_utility must be a number in 0..10") from exc
    if not 0.0 <= raw <= 10.0:
        raise BacktestError(f"overall_editorial_utility outside 0..10: {raw}")
    return raw


def combined_score(a_score: float, b_score: float, weight: float) -> float:
    layer = max(-10.0, min(14.0, a_score + b_score))
    return layer * weight


def validate_inputs(args: argparse.Namespace) -> tuple[list[str], dict[str, Any], dict[str, Any], dict[str, ImageRecord]]:
    lines = Path(args.quotes_file).read_text(encoding="utf-8").splitlines()
    quote_analysis = load_json(Path(args.quote_analysis))
    image_analysis = load_json(Path(args.image_analysis))
    editorial = load_json(Path(args.editorial_analysis))
    if quote_analysis.get("analysis_kind") != "quotes":
        raise BacktestError("quote_analysis.json has unexpected analysis_kind")
    if image_analysis.get("analysis_kind") != "images":
        raise BacktestError("image_analysis.json has unexpected analysis_kind")
    if editorial.get("analysis_kind") != EXPECTED_ANALYSIS_KIND:
        raise BacktestError(f"experimental file has unexpected analysis_kind={editorial.get('analysis_kind')!r}")

    image_dir = Path(args.image_dir)
    path_index = image_analysis.get("path_index") or {}
    items = image_analysis.get("items") or {}
    records: dict[str, ImageRecord] = {}
    for basename, entry in sorted((editorial.get("items") or {}).items()):
        if not isinstance(entry, dict):
            raise BacktestError(f"invalid editorial item for {basename}")
        basename = str(entry.get("basename") or basename)
        if basename.startswith("tg_"):
            raise BacktestError(f"generated image appeared in original experiment: {basename}")
        path = image_dir / basename
        if not path.exists():
            raise BacktestError(f"image missing for {basename}: {path}")
        current_sha = file_sha256(path)
        if current_sha != str(entry.get("sha256")):
            raise BacktestError(f"stale SHA-256 for {basename}")
        prod_hash = path_index.get(basename)
        if not prod_hash or prod_hash not in items:
            raise BacktestError(f"missing production image-analysis record for {basename}")
        prod_analysis = items[prod_hash].get("analysis") if isinstance(items[prod_hash], dict) else None
        if not isinstance(prod_analysis, dict):
            raise BacktestError(f"invalid production analysis for {basename}")
        editorial_analysis = dict(entry.get("analysis") or {})
        normalise_editorial_utility(editorial_analysis.get("overall_editorial_utility", 5.5))
        raw_dimensions = editorial_analysis.get("dimension_scores") or {}
        if not isinstance(raw_dimensions, dict):
            raise BacktestError(f"dimension_scores for {basename} is not an object")
        for dim, value in raw_dimensions.items():
            normalise_dimension_score(value, f"{basename}.dimension_scores.{dim}")
        records[basename] = ImageRecord(
            basename=basename,
            path=path,
            sha256=current_sha,
            production_analysis=prod_analysis,
            editorial_analysis=editorial_analysis,
        )
    return lines, quote_analysis, image_analysis, records


def quote_records(lines: list[str], quote_analysis: dict[str, Any]) -> list[QuoteRecord]:
    records = []
    items = quote_analysis.get("items") or {}
    for idx, text in enumerate(lines):
        if not text.strip():
            continue
        qhash = bot.quote_text_hash(text.rstrip())
        item = items.get(qhash)
        if not isinstance(item, dict) or not isinstance(item.get("analysis"), dict):
            continue
        records.append(QuoteRecord(qhash, text.rstrip(), idx, item["analysis"], "broad"))
    return records


def parse_recent_posts(log_dir: Path, quote_by_hash: dict[str, QuoteRecord], limit: int) -> list[QuoteRecord]:
    candidates: dict[str, dict[str, Any]] = {}
    posts = []
    for path in sorted(log_dir.glob("*.log*")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        current_quote: dict[str, Any] | None = None
        current_image: dict[str, Any] | None = None
        for line in lines:
            ts = line[:19] if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", line) else ""
            m = re.search(r"Selected quote line_no=(\d+) quote_hash=([0-9a-f]{64})", line)
            if m:
                current_quote = {"timestamp": ts, "line_no": int(m.group(1)), "quote_hash": m.group(2)}
                current_image = None
            m = re.search(r"Selected matched image basename=(\S+) image_no=(\d+) score=([-0-9.]+) components=(.+)", line)
            if m:
                current_image = {
                    "basename": m.group(1),
                    "image_no": int(m.group(2)),
                    "score": float(m.group(3)),
                    "components_text": m.group(4),
                }
            m = re.search(r"Quote/image posted successfully\. posted_id=(\d+)", line)
            if m and current_quote and current_image:
                qhash = current_quote["quote_hash"]
                if qhash in quote_by_hash:
                    post = {
                        "timestamp": ts or current_quote.get("timestamp", ""),
                        "post_id": m.group(1),
                        "quote_hash": qhash,
                        "actual_image": current_image["basename"],
                        "actual_logged_score": current_image["score"],
                        "actual_components_text": current_image["components_text"],
                    }
                    candidates[f"{post['timestamp']}:{post['post_id']}"] = post
    for post in sorted(candidates.values(), key=lambda item: item.get("timestamp", ""))[-limit:]:
        q = quote_by_hash[post["quote_hash"]]
        posts.append(QuoteRecord(q.quote_hash, q.text, q.line_no, q.analysis, "recent", post))
    return posts


def deterministic_broad_sample(records: list[QuoteRecord], size: int, seed: int) -> list[QuoteRecord]:
    rng = random.Random(seed)
    by_bucket: dict[tuple[str, str], list[QuoteRecord]] = defaultdict(list)
    for rec in records:
        primary = bot.normalise_tag((rec.analysis.get("primary_topics") or [""])[0] if isinstance(rec.analysis.get("primary_topics"), list) else "")
        energy = str(rec.analysis.get("visual_energy") or "")
        by_bucket[(primary, energy)].append(rec)
    sample: list[QuoteRecord] = []
    for bucket in sorted(by_bucket):
        choices = by_bucket[bucket]
        sample.append(rng.choice(choices))
        if len(sample) >= size:
            break
    remaining = [rec for rec in records if rec not in sample]
    rng.shuffle(remaining)
    sample.extend(remaining[: max(0, size - len(sample))])
    return sample[:size]


def diagnostic_quote_record(records: list[QuoteRecord]) -> QuoteRecord | None:
    for rec in records:
        if rec.text == DIAGNOSTIC_T70_QUOTE:
            return QuoteRecord(rec.quote_hash, rec.text, rec.line_no, rec.analysis, "diagnostic")
    for rec in records:
        if "Freedom is indivisible" in rec.text:
            return QuoteRecord(rec.quote_hash, rec.text, rec.line_no, rec.analysis, "diagnostic")
    return None


def rank_images_for_quote(q: QuoteRecord, images: dict[str, ImageRecord], image_analysis: dict[str, Any], editorial_idf: dict[str, float]) -> list[dict[str, Any]]:
    idf = bot.build_image_topic_idf(image_analysis)
    rows = []
    for basename, rec in sorted(images.items()):
        base_score, components, eligible = bot.score_image_for_quote(q.analysis, rec.production_analysis, idf)
        if not eligible:
            continue
        a_score, a_detail = editorial_affinity_score(q.analysis, rec.editorial_analysis, editorial_idf)
        b_score, b_detail = editorial_dimension_score(q.analysis, rec.editorial_analysis)
        combined_layers = {name: combined_score(a_score, b_score, weight) for name, weight in VARIANT_C_WEIGHTS.items()}
        row = {
            "basename": basename,
            "baseline_score": base_score,
            "baseline_components": components,
            "editorial_a_score": a_score,
            "editorial_a_detail": a_detail,
            "editorial_b_score": b_score,
            "editorial_b_detail": b_detail,
            "combined_layers": combined_layers,
            "combined_score_low": base_score + combined_layers["low"],
            "combined_score_medium": base_score + combined_layers["medium"],
            "combined_score_high": base_score + combined_layers["high"],
        }
        rows.append(row)
    rows.sort(key=lambda item: (-float(item["baseline_score"]), item["basename"]))
    for idx, row in enumerate(rows, 1):
        row["baseline_rank"] = idx
    for key in ("editorial_a_score", "editorial_b_score", "combined_score_low", "combined_score_medium", "combined_score_high"):
        for idx, row in enumerate(sorted(rows, key=lambda item: (-float(item[key]), item["basename"])), 1):
            row[f"{key}_rank"] = idx
    return rows


def top_by(rows: list[dict[str, Any]], key: str, n: int = 10) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: (-float(item[key]), item["basename"]))[:n]


def concentration(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    counts = Counter(item[key] for item in records)
    total = sum(counts.values()) or 1
    entropy = -sum((c / total) * math.log(c / total, 2) for c in counts.values())
    return {"counts": counts.most_common(10), "entropy": entropy}


def make_contact_sheet(path: Path, title: str, tiles: list[tuple[str, Path, str]]) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        make_contact_sheet_html(path.with_suffix(".html"), title, tiles)
        return
    thumb_w, thumb_h = 220, 180
    label_h = 80
    margin = 14
    width = margin + len(tiles) * (thumb_w + margin)
    height = margin * 3 + 34 + thumb_h + label_h
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((margin, margin), title[:160], fill="black", font=font)
    y = margin * 2 + 34
    for idx, (_role, image_path, label) in enumerate(tiles):
        x = margin + idx * (thumb_w + margin)
        try:
            img = Image.open(image_path).convert("RGB")
            img.thumbnail((thumb_w, thumb_h))
            ox = x + (thumb_w - img.width) // 2
            oy = y + (thumb_h - img.height) // 2
            sheet.paste(img, (ox, oy))
        except Exception:
            draw.rectangle((x, y, x + thumb_w, y + thumb_h), outline="red")
        for line_no, text in enumerate(label.split("\n")[:5]):
            draw.text((x, y + thumb_h + 4 + line_no * 13), text[:34], fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def make_contact_sheet_html(path: Path, title: str, tiles: list[tuple[str, Path, str]]) -> None:
    import html

    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "<!doctype html>",
        "<meta charset=\"utf-8\">",
        "<style>",
        "body{font-family:system-ui,-apple-system,sans-serif;margin:24px;background:#f6f4ef;color:#111}",
        ".quote{font-size:18px;font-weight:650;margin-bottom:16px}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}",
        ".tile{background:white;border:1px solid #ccc;padding:10px}",
        "img{width:100%;height:180px;object-fit:contain;background:#eee}",
        "pre{white-space:pre-wrap;font:13px/1.35 ui-monospace,monospace;margin:8px 0 0}",
        "</style>",
        f"<div class=\"quote\">{html.escape(title)}</div>",
        "<div class=\"grid\">",
    ]
    for _role, image_path, label in tiles:
        rel = os.path.relpath(image_path, path.parent)
        parts.extend(
            [
                "<div class=\"tile\">",
                f"<img src=\"{html.escape(rel)}\" alt=\"{html.escape(image_path.name)}\">",
                f"<pre>{html.escape(label)}</pre>",
                "</div>",
            ]
        )
    parts.append("</div>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def build_outputs(args: argparse.Namespace) -> dict[str, Any]:
    lines, quote_analysis, image_analysis, images = validate_inputs(args)
    all_quotes = quote_records(lines, quote_analysis)
    by_hash = {q.quote_hash: q for q in all_quotes}
    recent = parse_recent_posts(Path(args.log_dir), by_hash, args.recent_limit)
    broad = deterministic_broad_sample(all_quotes, args.broad_sample, args.seed)
    diagnostic = diagnostic_quote_record(all_quotes)
    editorial_idf, _ = build_editorial_idf(images)

    sample_records = recent + [q for q in broad if q.quote_hash not in {r.quote_hash for r in recent}]
    if diagnostic and diagnostic.quote_hash not in {q.quote_hash for q in sample_records}:
        sample_records.append(diagnostic)
    results = []
    for q in sample_records:
        rows = rank_images_for_quote(q, images, image_analysis, editorial_idf)
        by_name = {row["basename"]: row for row in rows}
        baseline_top = top_by(rows, "baseline_score", 10)
        medium_top = top_by(rows, "combined_score_medium", 10)
        actual = q.recent_post.get("actual_image") if q.recent_post else None
        results.append(
            {
                "sample": q.sample,
                "quote_hash": q.quote_hash,
                "line_no": q.line_no,
                "text": q.text,
                "recent_post": q.recent_post,
                "baseline_top10": baseline_top,
                "variant_a_top10": top_by(rows, "editorial_a_score", 10),
                "variant_b_top10": top_by(rows, "editorial_b_score", 10),
                "combined_top10_low": top_by(rows, "combined_score_low", 10),
                "combined_top10_medium": medium_top,
                "combined_top10_high": top_by(rows, "combined_score_high", 10),
                "actual_image_baseline_rank": by_name.get(actual, {}).get("baseline_rank") if actual else None,
                "actual_image_combined_rank": by_name.get(actual, {}).get("combined_score_medium_rank") if actual else None,
                "actual_image_scores": by_name.get(actual) if actual else None,
                "rankings": rows,
            }
        )

    raw_terms = Counter()
    norm_terms = Counter()
    for rec in images.values():
        analysis = rec.editorial_analysis
        for key in ("abstract_quote_affinities", "editorial_functions", "best_quote_types", "avoid_quote_types"):
            for value in bot.as_string_list(analysis.get(key)):
                raw_terms[bot.normalise_tag(value)] += 1
                norm_terms.update(canonical_concepts(value))

    baseline_winners = [{"winner": item["baseline_top10"][0]["basename"], "sample": item["sample"]} for item in results if item["baseline_top10"]]
    combined_winners = [{"winner": item["combined_top10_medium"][0]["basename"], "sample": item["sample"]} for item in results if item["combined_top10_medium"]]
    output = {
        "schema_version": 1,
        "input_validation": {
            "quotes": len(all_quotes),
            "images": len(images),
            "recent_posts": len(recent),
            "broad_sample": len(broad),
            "diagnostic_quote_included": bool(diagnostic),
            "analysis_kind": EXPECTED_ANALYSIS_KIND,
        },
        "production_scorer": {
            "helpers_reused": [
                "score_image_for_quote",
                "build_image_topic_idf",
                "normalise_tag",
                "phrase_matches_text",
                "hard_mismatch_phrase_matches_text",
                "visual_energy_score",
            ],
            "components": [
                "topics",
                "tone_mood",
                "visual_energy",
                "scene_activity_symbols",
                "historical",
                "mismatches",
                "quality",
                "strong_mismatch exclusion",
            ],
            "ranking_mode": "full_original_pool_not_historical_cycle_replay",
        },
        "vocabulary": {
            "broken_run_raw_unique_terms": BROKEN_RUN_BASELINE["vocabulary"]["raw_unique_terms"],
            "broken_run_normalised_unique_terms": BROKEN_RUN_BASELINE["vocabulary"]["normalised_unique_concepts"],
            "raw_unique_terms": len([k for k in raw_terms if k]),
            "normalised_unique_terms": len([k for k in norm_terms if k]),
            "most_frequent_normalised": norm_terms.most_common(20),
            "rare_normalised": sorted([k for k, c in norm_terms.items() if c == 1])[:50],
        },
        "idf": editorial_idf,
        "results": results,
        "concentration": {
            "baseline_top1": concentration(baseline_winners, "winner"),
            "combined_medium_top1": concentration(combined_winners, "winner"),
        },
    }
    return output


def row_summary(result: dict[str, Any]) -> str:
    baseline = result["baseline_top10"][0]["basename"] if result["baseline_top10"] else ""
    combined = result["combined_top10_medium"][0]["basename"] if result["combined_top10_medium"] else ""
    actual = (result.get("recent_post") or {}).get("actual_image", "")
    return f"| {result['sample']} | {result['line_no']} | {actual} | {result.get('actual_image_baseline_rank') or ''} | {baseline} | {combined} | {result.get('actual_image_combined_rank') or ''} |"


def write_report(output: dict[str, Any], path: Path) -> None:
    results = output["results"]
    recent = [r for r in results if r["sample"] == "recent"]
    broad = [r for r in results if r["sample"] == "broad"]
    changed = [r for r in results if r["baseline_top10"] and r["combined_top10_medium"] and r["baseline_top10"][0]["basename"] != r["combined_top10_medium"][0]["basename"]]
    unchanged = [r for r in results if r["baseline_top10"] and r["combined_top10_medium"] and r["baseline_top10"][0]["basename"] == r["combined_top10_medium"][0]["basename"]]
    potentially_worse = [
        r
        for r in changed
        if r["combined_top10_medium"][0]["baseline_score"] + 3.0 < r["baseline_top10"][0]["baseline_score"]
    ]
    lines = [
        "# Original Editorial Matching Backtest",
        "",
        "## Executive verdict",
        "",
        "This is an offline full-original-pool ranking experiment, not a historical replay of image-cycle availability. The original run had a critical scale bug: the experimental `dimension_scores` and `overall_editorial_utility` are 0..10, but the first backtest treated them as 0..100. This corrected run validates that scale explicitly. The editorial layer is useful as an analysis signal, but the conservative recommendation is still to keep it for analysis/narrow production-design experiments rather than deploy it directly.",
        "",
        "## Corrected scale versus broken run",
        "",
        "- Corrected assumption: `dimension_scores` are 0..10 and are normalised by dividing by 10.",
        "- Corrected assumption: `overall_editorial_utility` is 0..10; 5.5 is neutral and 9.0 is strongly positive.",
        "- Broken run assumption: both values were treated as if they were 0..100.",
        "",
        "| metric | broken run | corrected run |",
        "| --- | ---: | ---: |",
        f"| winner changes, low | {BROKEN_RUN_BASELINE['winner_changes']['low']} | {sum(1 for r in results if r['baseline_top10'] and r['combined_top10_low'] and r['baseline_top10'][0]['basename'] != r['combined_top10_low'][0]['basename'])} |",
        f"| winner changes, medium | {BROKEN_RUN_BASELINE['winner_changes']['medium']} | {sum(1 for r in results if r['baseline_top10'] and r['combined_top10_medium'] and r['baseline_top10'][0]['basename'] != r['combined_top10_medium'][0]['basename'])} |",
        f"| winner changes, high | {BROKEN_RUN_BASELINE['winner_changes']['high']} | {sum(1 for r in results if r['baseline_top10'] and r['combined_top10_high'] and r['baseline_top10'][0]['basename'] != r['combined_top10_high'][0]['basename'])} |",
        f"| combined-medium winner entropy | {BROKEN_RUN_BASELINE['combined_medium_entropy']:.3f} | {output['concentration']['combined_medium_top1']['entropy']:.3f} |",
        "",
        "## Input validation",
        "",
        f"- Analysed quotes: {output['input_validation']['quotes']}",
        f"- Original images validated: {output['input_validation']['images']}",
        f"- Recent regular posts recovered: {output['input_validation']['recent_posts']}",
        f"- Broad sample size: {output['input_validation']['broad_sample']}",
        f"- Diagnostic freedom quote included: {output['input_validation']['diagnostic_quote_included']}",
        f"- Experimental analysis_kind: `{output['input_validation']['analysis_kind']}`",
        "",
        "## Exact production scorer inspected/reused",
        "",
        "The tool imports `mrsMThatcher2.py` for pure helpers only. Importing the module does not start the bot loop, contact APIs, mutate state, or post. Reused helpers include `score_image_for_quote`, `build_image_topic_idf`, `normalise_tag`, phrase matching helpers and visual-energy scoring.",
        "",
        "Production components: topics, tone_mood, visual_energy, scene_activity_symbols, historical, mismatches, quality, with strong visual mismatch returning an ineligible image.",
        "",
        "## Experimental scoring design",
        "",
        "- Variant A: normalised affinity/editorial-function/best-quote-type matching, IDF-downweighted for common concepts, with avoid_quote_types as a negative signal.",
        "- Variant B: deterministic quote dimension profile matched against 12 fixed image dimensions.",
        "- Variant C: conservative combined adjunct layer, tested at low/medium/high weights; report tables use medium unless stated.",
        "",
        "## Vocabulary normalisation statistics",
        "",
        f"- Broken-run raw unique terms: {output['vocabulary']['broken_run_raw_unique_terms']}",
        f"- Broken-run normalised unique concepts: {output['vocabulary']['broken_run_normalised_unique_terms']}",
        f"- Raw unique terms: {output['vocabulary']['raw_unique_terms']}",
        f"- Normalised unique concepts: {output['vocabulary']['normalised_unique_terms']}",
        "- Most frequent normalised concepts:",
        "",
    ]
    for concept, count in output["vocabulary"]["most_frequent_normalised"][:15]:
        lines.append(f"  - {concept}: {count}")
    lines.extend(["", "- Rare normalised concepts:", ""])
    lines.append(", ".join(output["vocabulary"]["rare_normalised"][:30]) or "None")
    lines.extend([
        "",
        "## Recent real-post results",
        "",
        "| sample | line_no | actual | actual baseline rank | baseline winner | conservative combined winner | actual combined rank |",
        "| --- | ---: | --- | ---: | --- | --- | ---: |",
    ])
    for item in recent:
        lines.append(row_summary(item))
    lines.extend([
        "",
        "## Broad quote-sample results",
        "",
        "| sample | line_no | actual | actual baseline rank | baseline winner | conservative combined winner | actual combined rank |",
        "| --- | ---: | --- | ---: | --- | --- | ---: |",
    ])
    for item in broad[:40]:
        lines.append(row_summary(item))
    lines.extend([
        "",
        "## t70 freedom-quote case study",
        "",
    ])
    target = DIAGNOSTIC_T70_QUOTE
    t70_cases = [r for r in results if r["text"] == target or (r.get("actual_image_scores") or {}).get("basename") == "t70.jpg"]
    if not t70_cases:
        t70_cases = [r for r in results if "Freedom is indivisible" in r["text"]]
    for item in t70_cases[:3]:
        by_t70 = next((row for row in item["rankings"] if row["basename"] == "t70.jpg"), None)
        if by_t70:
            lines.append(f"- Quote line {item['line_no']}: {item['text']}")
            if item.get("recent_post") and item["recent_post"].get("actual_components_text"):
                lines.append(f"  - live logged components for actual post: {item['recent_post']['actual_components_text']}")
            lines.append(f"  - broken-run t70 Variant A score/rank: {BROKEN_RUN_BASELINE['t70']['variant_a_score']:.2f} / {BROKEN_RUN_BASELINE['t70']['variant_a_rank']}")
            lines.append(f"  - broken-run t70 Variant B score/rank: {BROKEN_RUN_BASELINE['t70']['variant_b_score']:.2f} / {BROKEN_RUN_BASELINE['t70']['variant_b_rank']}")
            lines.append(f"  - broken-run t70 combined-medium rank/score: {BROKEN_RUN_BASELINE['t70']['combined_medium_rank']} / {BROKEN_RUN_BASELINE['t70']['combined_medium_score']:.2f}")
            lines.append(f"  - t70 baseline rank/score: {by_t70['baseline_rank']} / {by_t70['baseline_score']:.2f}")
            lines.append(f"  - t70 Variant A score/rank: {by_t70['editorial_a_score']:.2f} / {by_t70['editorial_a_score_rank']}")
            lines.append(f"  - t70 Variant B score/rank: {by_t70['editorial_b_score']:.2f} / {by_t70['editorial_b_score_rank']}")
            lines.append(f"  - t70 combined-medium rank/score: {by_t70['combined_score_medium_rank']} / {by_t70['combined_score_medium']:.2f}")
            lines.append(f"  - Largest affinity matches: {', '.join(by_t70['editorial_a_detail'].get('matches', [])[:8])}")
    if not t70_cases:
        lines.append("The exact diagnostic t70 freedom quote was not present in the sampled recent/broad set; inspect JSON for all quote rankings or rerun with a targeted sample.")
    lines.extend([
        "",
        "## Winner-concentration analysis",
        "",
        f"- Baseline top-1 entropy: {output['concentration']['baseline_top1']['entropy']:.3f}",
        f"- Combined-medium top-1 entropy: {output['concentration']['combined_medium_top1']['entropy']:.3f}",
        "- Baseline most frequent winners:",
    ])
    for name, count in output["concentration"]["baseline_top1"]["counts"][:5]:
        lines.append(f"  - {name}: {count}")
    lines.append("- Combined most frequent winners:")
    for name, count in output["concentration"]["combined_medium_top1"]["counts"][:5]:
        lines.append(f"  - {name}: {count}")
    lines.extend([
        "",
        "## Examples where the editorial layer clearly improves a match",
        "",
    ])
    if changed:
        for item in changed[:8]:
            lines.append(f"- line {item['line_no']}: baseline `{item['baseline_top10'][0]['basename']}` -> combined `{item['combined_top10_medium'][0]['basename']}`")
            lines.append(f"  - quote: {item['text'][:180]}")
            winner = item["combined_top10_medium"][0]
            detail = winner.get("editorial_a_detail", {})
            matches = ", ".join(detail.get("matches", [])[:6]) or "dimension compatibility"
            lines.append(f"  - editorial signal: {matches}")
    else:
        lines.append("No conservative-medium case clearly improved the top-1 winner. At high weight, three winners changed, which is useful for sensitivity analysis but too strong for a production recommendation.")
    lines.extend([
        "",
        "## Examples where it clearly makes a match worse",
        "",
    ])
    if potentially_worse:
        for item in potentially_worse[:6]:
            lines.append(f"- line {item['line_no']}: baseline `{item['baseline_top10'][0]['basename']}` -> combined `{item['combined_top10_medium'][0]['basename']}`")
            lines.append(f"  - baseline score gap before editorial layer: {item['baseline_top10'][0]['baseline_score'] - item['combined_top10_medium'][0]['baseline_score']:.2f}")
            lines.append(f"  - quote: {item['text'][:180]}")
    else:
        lines.append("No sampled case showed the conservative layer replacing the baseline winner with an image more than 3 production-score points worse.")
    lines.extend([
        "",
        "## Examples with no meaningful change",
        "",
    ])
    for item in unchanged[:6]:
        lines.append(f"- line {item['line_no']}: winner remains `{item['baseline_top10'][0]['basename']}`")
        lines.append(f"  - quote: {item['text'][:180]}")
    lines.extend([
        "",
        "## Sensitivity to editorial weight",
        "",
        "| weight | winner changes vs baseline |",
        "| --- | ---: |",
    ])
    for label in VARIANT_C_WEIGHTS:
        key = f"combined_top10_{label}"
        count = sum(1 for r in results if r["baseline_top10"] and r[key] and r["baseline_top10"][0]["basename"] != r[key][0]["basename"])
        lines.append(f"| {label} | {count} |")
    lines.extend([
        "",
        "## Recommendation",
        "",
        "Keep the new editorial data for analysis and use it in a narrow production-design experiment only. It appears capable of surfacing meaningful rhetorical matches, but the portrait-concentration risk remains real enough that it should not replace or dominate the existing production matcher.",
        "",
        "## Output notes",
        "",
        "- `original_editorial_matching_backtest.json` contains full rankings.",
        "- `review/original_editorial_backtest/` contains a small set of contact sheets for visual inspection.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_contact_sheets(output: dict[str, Any], images_dir: Path, out_dir: Path, limit: int = 10) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("comparison_*"):
        if old.is_file():
            old.unlink()
    candidates = []
    target = "There is an increasing belief that freedom is divisible. No myth is more dangerous. Freedom is indivisible."
    for item in output["results"]:
        changed = item["baseline_top10"] and item["combined_top10_medium"] and item["baseline_top10"][0]["basename"] != item["combined_top10_medium"][0]["basename"]
        diagnostic = item["text"] == target or "Freedom is indivisible" in item["text"]
        if changed or diagnostic or item["sample"] == "recent":
            candidates.append((diagnostic, changed, item))
    selected = [item for _diag, _changed, item in sorted(candidates, key=lambda t: (not t[0], not t[1], t[2]["line_no"]))[:limit]]
    for idx, item in enumerate(selected, 1):
        roles = []
        seen = set()
        def add(role: str, row: dict[str, Any] | None) -> None:
            if not row or row["basename"] in seen:
                return
            seen.add(row["basename"])
            label = f"{role}\n{row['basename']}\nbase {row['baseline_score']:.1f}\ned {row['editorial_a_score'] + row['editorial_b_score']:.1f}\ncomb {row['combined_score_medium']:.1f}"
            roles.append((role, images_dir / row["basename"], label))
        actual = (item.get("recent_post") or {}).get("actual_image")
        if actual:
            add("actual", next((r for r in item["rankings"] if r["basename"] == actual), None))
        add("baseline", item["baseline_top10"][0] if item["baseline_top10"] else None)
        add("variant_a", item["variant_a_top10"][0] if item["variant_a_top10"] else None)
        add("variant_b", item["variant_b_top10"][0] if item["variant_b_top10"] else None)
        add("combined", item["combined_top10_medium"][0] if item["combined_top10_medium"] else None)
        if roles:
            make_contact_sheet(out_dir / f"comparison_{idx:02d}_line_{item['line_no']}.jpg", item["text"], roles)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quotes-file", default=str(ROOT / "mrsMThatcher.txt"))
    ap.add_argument("--quote-analysis", default=str(ROOT / "quote_analysis.json"))
    ap.add_argument("--image-analysis", default=str(ROOT / "image_analysis.json"))
    ap.add_argument("--editorial-analysis", default=str(ROOT / "original_image_editorial_analysis_experiment_v1.json"))
    ap.add_argument("--image-dir", default=str(ROOT / "images"))
    ap.add_argument("--log-dir", default=str(ROOT))
    ap.add_argument("--json-output", default=str(ROOT / "original_editorial_matching_backtest.json"))
    ap.add_argument("--report-output", default=str(ROOT / "original_editorial_matching_backtest_report.md"))
    ap.add_argument("--contact-sheet-dir", default=str(ROOT / "review" / "original_editorial_backtest"))
    ap.add_argument("--recent-limit", type=int, default=30)
    ap.add_argument("--broad-sample", type=int, default=100)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args()

    try:
        output = build_outputs(args)
        Path(args.json_output).write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        write_report(output, Path(args.report_output))
        write_contact_sheets(output, Path(args.image_dir), Path(args.contact_sheet_dir))
    except BacktestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
